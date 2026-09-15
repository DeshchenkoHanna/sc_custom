"""Download Item Attachments — pack the item drawings of a Purchase Order into one ZIP.

Used by the "Download Item Attachments" button on Purchase Order.
Collects the .pdf / .stp / .step files attached to the Item master of every
row in the item table and returns them as a single flat ZIP named
``<PO-name>-item-attachments.zip``. Files keep their original names; when a
name is already taken the later file gets a ``_1``, ``_2``, ... suffix before
the extension (PO row order, then upload date).

Attachments of the Purchase Order itself are deliberately not included.
Items without a matching attachment are skipped.
"""

import io
import os
import re
import zipfile

import frappe
from frappe import _
from frappe.core.doctype.file.file import has_permission as file_has_permission

ALLOWED_EXTENSIONS = (".pdf", ".stp", ".step")
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_PATH_SEPARATORS = re.compile(r"[\\/]+")


@frappe.whitelist()
def get_item_attachment_summary(purchase_order: str) -> dict:
    """Return how many matching files each item of the PO has (no content).

    The client calls this before opening the save dialog so it can tell the
    user when there is nothing to download.
    """
    items = _get_item_codes(purchase_order)
    files_by_item = _collect_files(items)

    summary = [
        {"item_code": item_code, "file_count": len(files_by_item.get(item_code, []))}
        for item_code in items
    ]
    return {
        "items": summary,
        "total_files": sum(s["file_count"] for s in summary),
        "items_with_files": sum(1 for s in summary if s["file_count"]),
        "items_without_files": sum(1 for s in summary if not s["file_count"]),
    }


@frappe.whitelist()
def download_item_attachments(purchase_order: str):
    """Stream a ZIP with the item attachments of the PO as a file download."""
    items = _get_item_codes(purchase_order)
    files_by_item = _collect_files(items)

    if not any(files_by_item.values()):
        frappe.throw(
            _("No .pdf, .stp or .step attachments found on the items of this Purchase Order."),
            title=_("No attachments"),
        )

    used_names: set[str] = set()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item_code in items:
            for entry in files_by_item.get(item_code, []):
                zf.write(entry["path"], arcname=_unique_name(entry["file_name"], used_names))

    frappe.response.filename = f"{_UNSAFE_CHARS.sub('_', purchase_order)}-item-attachments.zip"
    frappe.response.filecontent = buffer.getvalue()
    frappe.response.type = "download"


def _unique_name(file_name: str, used_names: set[str]) -> str:
    """Return the original name, or ``stem_<n>.ext`` when that name is already taken.

    Names are compared case-insensitively so the ZIP also unpacks cleanly on
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
