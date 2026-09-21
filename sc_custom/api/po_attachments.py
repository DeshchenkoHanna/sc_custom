"""Create Item Attachments — pack the item drawings of a Purchase Order into one archive.

Used by the "Create Item Attachments" button on Purchase Order (FEAT-201).
Collects the drawing / CAD files (.pdf, .stp, .step, .frl3, .vikx, .dxf) attached
to the Item master of every row in the item table, packs them into a single flat
archive (ZIP or 7z, chosen by the user) and attaches it to the Purchase Order as a
**private** file named ``YYYYMMDD_<PO-name>.zip|.7z`` (posting date of the PO at
generation time). Everyone with read permission on the PO can download it from the
attachments sidebar; creating it requires write permission.

Inside the archive files keep their original names; when a name is already taken
the later file gets a ``_1``, ``_2``, ... suffix before the extension (PO row
order, then upload date). Attachments of the Purchase Order itself are not
included. Items without a matching attachment are skipped.

Lifecycle: a second run replaces the previous archive. The archive is removed when
the PO is Cancelled (``on_cancel`` doc event) or Closed (wrapper around ERPNext's
``update_status``, because "Close" writes the status with ``db_set`` and fires no
doc event). Completed POs keep it.
"""

import io
import os
import re
import zipfile

import frappe
from frappe import _
from frappe.core.doctype.file.file import has_permission as file_has_permission
from frappe.utils import getdate
from frappe.utils.file_manager import get_max_file_size

ALLOWED_EXTENSIONS = (".pdf", ".stp", ".step", ".frl3", ".vikx", ".dxf")
ARCHIVE_FORMATS = ("zip", "7z")
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_PATH_SEPARATORS = re.compile(r"[\\/]+")


# --------------------------------------------------------------------------- API


@frappe.whitelist()
def get_item_attachment_summary(purchase_order: str) -> dict:
    """Return how many matching files each item of the PO has and whether an
    archive already exists (no content is built). The client calls this before
    showing the format / replace dialog."""
    items = _get_item_codes(purchase_order)
    files_by_item = _collect_files(items)
    existing = _find_existing_archives(purchase_order)

    summary = _summarize(items, files_by_item)
    summary["existing_archive"] = existing[0].file_name if existing else None
    return summary


@frappe.whitelist()
def create_item_attachments(purchase_order: str, archive_format: str = "zip") -> dict:
    """Build the archive, replace any previous one and attach it to the PO as a private file."""
    archive_format = (archive_format or "zip").lower()
    if archive_format not in ARCHIVE_FORMATS:
        frappe.throw(_("Unsupported archive format: {0}").format(archive_format))

    frappe.has_permission("Purchase Order", "write", purchase_order, throw=True)
    po = frappe.db.get_value(
        "Purchase Order", purchase_order, ["transaction_date", "status", "docstatus"], as_dict=True
    )
    if not po:
        frappe.throw(_("Purchase Order {0} not found.").format(purchase_order), frappe.DoesNotExistError)
    if po.docstatus == 2 or po.status == "Closed":
        frappe.throw(_("Item attachments cannot be created for a Cancelled or Closed Purchase Order."))

    items = _get_item_codes(purchase_order)
    files_by_item = _collect_files(items)
    if not any(files_by_item.values()):
        frappe.throw(
            _("No matching attachments ({0}) found on the items of this Purchase Order.").format(
                ", ".join(ALLOWED_EXTENSIONS)
            ),
            title=_("No attachments"),
        )

    _validate_archive_allowed(archive_format)
    content = _build_archive(purchase_order, items, files_by_item, archive_format)
    _validate_archive_size(content)
    file_name = "{date}_{po}.{ext}".format(
        date=getdate(po.transaction_date).strftime("%Y%m%d"),
        po=_UNSAFE_CHARS.sub("_", purchase_order),
        ext=archive_format,
    )

    # Frappe deletes the physical file immediately in File.on_trash (not at commit). If the
    # insert below fails, the DB rollback restores the old File rows but not their files, so
    # keep the old bytes in memory and put them back on disk before re-raising.
    previous = _snapshot_existing_archives(purchase_order)
    replaced = remove_item_attachments(purchase_order)
    try:
        file_doc = _insert_archive_file(purchase_order, file_name, content)
    except Exception:
        _restore_files_on_disk(previous)
        raise

    result = _summarize(items, files_by_item)
    result.update(
        {
            "file_name": file_doc.file_name,
            "file_url": file_doc.file_url,
            "archive_format": archive_format,
            "replaced": replaced,
        }
    )
    return result


# ------------------------------------------------------------------ lifecycle hooks


def on_purchase_order_cancel(doc, method=None):
    """doc_events hook: drop the archive when the PO is cancelled."""
    remove_item_attachments(doc.name)


@frappe.whitelist()
def update_status(status, name):
    """override_whitelisted_methods wrapper around ERPNext's Purchase Order
    ``update_status`` (Close / Re-open / Deliver). Runs the original first, then
    removes the archive when the PO was closed."""
    from erpnext.buying.doctype.purchase_order.purchase_order import (
        update_status as erpnext_update_status,
    )

    result = erpnext_update_status(status, name)
    if status == "Closed":
        remove_item_attachments(name)
    return result


def remove_item_attachments(purchase_order: str) -> list[str]:
    """Delete every archive generated by this feature for the PO. Returns the file names."""
    removed = []
    for row in _find_existing_archives(purchase_order):
        frappe.delete_doc("File", row.name, ignore_permissions=True, delete_permanently=True)
        removed.append(row.file_name)
    return removed


# ---------------------------------------------------------------------- helpers


def _insert_archive_file(purchase_order: str, file_name: str, content: bytes):
    """Attach the archive bytes to the PO as a private File.

    Permission (PO write) is checked by the caller; inserting as the user would
    additionally require File create rights, which not every buyer role has.
    """
    return frappe.get_doc(
        {
            "doctype": "File",
            "file_name": file_name,
            "attached_to_doctype": "Purchase Order",
            "attached_to_name": purchase_order,
            "is_private": 1,
            "content": content,
        }
    ).insert(ignore_permissions=True)


def _validate_archive_allowed(archive_format: str) -> None:
    """Same rule File.validate_file_extension applies to uploads, checked up front so a
    blocked format fails before the previous archive is removed."""
    allowed = frappe.get_system_settings("allowed_file_extensions")
    if allowed and archive_format.upper() not in allowed.splitlines():
        frappe.throw(
            _("File type {0} is not allowed by System Settings (Allowed File Extensions).").format(
                archive_format.upper()
            )
        )


def _validate_archive_size(content: bytes) -> None:
    max_size = get_max_file_size()
    if max_size and len(content) > max_size:
        frappe.throw(
            _("The archive is {0} MB, larger than the maximum file size of {1} MB.").format(
                round(len(content) / 1024 / 1024, 1), round(max_size / 1024 / 1024, 1)
            )
        )


def _snapshot_existing_archives(purchase_order: str) -> list[tuple[str, bytes]]:
    """(disk path, bytes) of every existing archive of the PO that is present on disk."""
    snapshot = []
    for row in _find_existing_archives(purchase_order):
        path = frappe.get_doc("File", row.name).get_full_path()
        if path and os.path.isfile(path):
            with open(path, "rb") as fh:
                snapshot.append((path, fh.read()))
    return snapshot


def _restore_files_on_disk(snapshot: list[tuple[str, bytes]]) -> None:
    for path, data in snapshot:
        try:
            if not os.path.exists(path):
                with open(path, "wb") as fh:
                    fh.write(data)
        except OSError:
            frappe.log_error(title="po_attachments: could not restore previous archive on disk")


def _summarize(item_codes: list[str], files_by_item: dict[str, list[dict]]) -> dict:
    per_item = [
        {"item_code": code, "file_count": len(files_by_item.get(code, []))} for code in item_codes
    ]
    return {
        "items": per_item,
        "total_files": sum(s["file_count"] for s in per_item),
        "items_with_files": sum(1 for s in per_item if s["file_count"]),
        "items_without_files": sum(1 for s in per_item if not s["file_count"]),
    }


def _find_existing_archives(purchase_order: str) -> list[dict]:
    """Archives created by this feature: files attached to the PO named ``YYYYMMDD_<PO>.zip|7z``."""
    # Frappe appends a 6-hex-char suffix to the name if the path is already taken on disk
    # (e.g. the previous archive's file survived because another File shares its content)
    pattern = re.compile(
        r"^\d{8}_" + re.escape(_UNSAFE_CHARS.sub("_", purchase_order)) + r"(?:[0-9a-f]{6})?\.(zip|7z)$",
        re.IGNORECASE,
    )
    rows = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Purchase Order",
            "attached_to_name": purchase_order,
            "is_folder": 0,
        },
        fields=["name", "file_name", "file_url", "creation"],
        order_by="creation desc",
    )
    return [row for row in rows if row.file_name and pattern.match(row.file_name)]


def _build_archive(
    purchase_order: str,
    item_codes: list[str],
    files_by_item: dict[str, list[dict]],
    archive_format: str,
) -> bytes:
    used_names: set[str] = set()
    entries = [
        (entry["path"], _unique_name(entry["file_name"], used_names))
        for item_code in item_codes
        for entry in files_by_item.get(item_code, [])
    ]

    buffer = io.BytesIO()
    if archive_format == "7z":
        try:
            import py7zr
        except ImportError:
            frappe.throw(_("7z support is not installed on the server (Python package py7zr)."))
        # Two POs with the same item files would otherwise produce byte-identical archives,
        # and Frappe's content-hash dedup would point the second PO's attachment at the
        # first PO's file on disk (wrong name on download). 7z has no archive comment, so
        # the entries are written from a stream: py7zr then stamps them with the current
        # time (100 ns resolution), which makes every archive unique. Side effect: file
        # dates inside a 7z are the archive creation time, not the upload date in ERP.
        with py7zr.SevenZipFile(buffer, "w") as archive:
            for path, arcname in entries:
                with open(path, "rb") as fh:
                    archive.writef(fh, arcname)
    else:
        # Same uniqueness concern as above; ZIP supports an archive comment, so file
        # dates inside the ZIP can stay as on disk (= upload date in ERP).
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.comment = purchase_order.encode("utf-8")
            for path, arcname in entries:
                zf.write(path, arcname=arcname)
    return buffer.getvalue()


def _unique_name(file_name: str, used_names: set[str]) -> str:
    """Return the original name, or ``stem_<n>.ext`` when that name is already taken.

    Names are compared case-insensitively so the archive also unpacks cleanly on
    Windows. ``used_names`` is updated with the name that was handed out.
    """
    base = _PATH_SEPARATORS.sub("_", (file_name or "").strip()) or "file"
    stem, ext = os.path.splitext(base)

    candidate = base
    n = 0
    while candidate.lower() in used_names:
        n += 1
        candidate = f"{stem}_{n}{ext}"

    used_names.add(candidate.lower())
    return candidate


def _get_item_codes(purchase_order: str) -> list[str]:
    """Distinct item codes of the saved PO rows, in row order."""
    if not purchase_order:
        frappe.throw(_("Purchase Order is required."))

    frappe.has_permission("Purchase Order", "read", purchase_order, throw=True)

    rows = frappe.get_all(
        "Purchase Order Item",
        filters={"parent": purchase_order, "parenttype": "Purchase Order"},
        fields=["item_code"],
        order_by="idx asc",
    )

    seen = set()
    item_codes = []
    for row in rows:
        code = row.item_code
        if code and code not in seen:
            seen.add(code)
            item_codes.append(code)
    return item_codes


def _collect_files(item_codes: list[str]) -> dict[str, list[dict]]:
    """Map item_code -> ordered list of {path, file_name} for downloadable attachments.

    Only local files with an allowed extension that exist on disk and that the
    current user may read are returned, ordered by upload date. The same
    physical file attached twice to one item is listed once; the same file on
    two different items is listed for each of them.
    """
    result: dict[str, list[dict]] = {code: [] for code in item_codes}
    if not item_codes:
        return result

    candidates = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Item",
            "attached_to_name": ["in", item_codes],
            "is_folder": 0,
        },
        fields=["name", "attached_to_name", "file_name", "file_url"],
        order_by="creation asc, name asc",
    )

    # DB matching is case-insensitive; map back to the exact item code of the PO row
    code_by_lower = {code.lower(): code for code in item_codes}
    seen_urls: dict[str, set] = {code: set() for code in item_codes}
    for row in candidates:
        item_code = code_by_lower.get((row.attached_to_name or "").lower())
        if not item_code:
            continue
        ext = os.path.splitext(row.file_name or row.file_url or "")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue
        if not row.file_url or row.file_url.startswith(("http://", "https://")):
            continue
        if row.file_url in seen_urls[item_code]:
            continue

        file_doc = frappe.get_doc("File", row.name)
        if not file_has_permission(file_doc, "read"):
            continue

        path = file_doc.get_full_path()
        if not path or not os.path.isfile(path):
            continue

        seen_urls[item_code].add(row.file_url)
        result[item_code].append(
            {"path": path, "file_name": row.file_name or os.path.basename(row.file_url)}
        )

    return result
