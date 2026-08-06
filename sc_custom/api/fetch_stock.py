"""Fetch Stock — populate Stock Entry items from a Warehouse + Storage.

Used by the "Fetch Stock" button on Stock Entry (Manufacture / Repack).
Returns fully-populated source (consumed) rows for every item currently on
hand at the selected warehouse + storage — filled exactly as if the user had
picked the item manually (cost center, expense account, uom, valuation rate),
with a Serial & Batch Bundle created for batch/serial-tracked items.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowtime, today

from sc_custom.api.queries import (
    get_auto_batch_nos_with_storage,
    get_auto_serial_nos_with_storage,
    set_bundle_storage,
)


@frappe.whitelist()
def get_stock_items_by_storage(warehouse, storage, parent_doc=None):
    """Return Stock Entry Detail row dicts for stock at warehouse + storage.

    Bin has no storage dimension, so current on-hand per item is the sum of
    Stock Ledger Entry.actual_qty for that warehouse + storage. Batch-tracked
    items emit one row per batch (the storage per-batch check assumes a single
    batch per row); serial-tracked items emit a single row with all serials.
    """
    if not warehouse or not storage:
        frappe.throw(_("Both Warehouse and Storage are required."))

    parent = frappe.parse_json(parent_doc) if parent_doc else {}
    company = parent.get("company") or frappe.defaults.get_user_default("Company")
    if not company:
        frappe.throw(_("Please set the Company on the Stock Entry first."))

    purpose = parent.get("purpose") or "Material Issue"
    posting_date = parent.get("posting_date") or today()
    posting_time = parent.get("posting_time") or nowtime()

    # In-memory Stock Entry used only for get_item_details context.
    ste = frappe.new_doc("Stock Entry")
    ste.company = company
    ste.purpose = purpose
    ste.stock_entry_type = parent.get("stock_entry_type") or purpose
    ste.posting_date = posting_date
    ste.posting_time = posting_time
    ste.from_warehouse = warehouse

    parent_ctx = {
        "doctype": "Stock Entry",
        "purpose": purpose,
        "company": company,
        "posting_date": posting_date,
        "posting_time": posting_time,
    }

    balances = frappe.db.sql(
        """
        SELECT item_code, SUM(actual_qty) AS qty
        FROM `tabStock Ledger Entry`
        WHERE warehouse = %(warehouse)s
          AND storage = %(storage)s
          AND is_cancelled = 0
        GROUP BY item_code
        HAVING SUM(actual_qty) > 0.000001
        ORDER BY item_code
        """,
        {"warehouse": warehouse, "storage": storage},
        as_dict=True,
    ) or []

    rows = []
    for bal in balances:
        item = frappe.db.get_value(
            "Item", bal.item_code, ["has_batch_no", "has_serial_no"], as_dict=True
        )
        if not item:
            continue

        try:
            if item.has_serial_no:
                serials = get_auto_serial_nos_with_storage(
                    bal.item_code, warehouse, storage, bal.qty
                )
                if not serials:
                    continue
                entries = [{"serial_no": s["serial_no"]} for s in serials]
                rows.append(
                    _build_row(
                        ste, parent_ctx, bal.item_code, warehouse, storage,
                        len(serials), entries,
                    )
                )

            elif item.has_batch_no:
                batches = get_auto_batch_nos_with_storage(
                    bal.item_code, warehouse, storage, bal.qty
                )
                if not batches:
                    continue
                # One row per batch — the per-storage batch check assumes a
                # single batch per row (see reference_storage_dimension).
                for b in batches:
                    entries = [{"batch_no": b["batch_no"], "qty": flt(b["qty"])}]
                    rows.append(
                        _build_row(
                            ste, parent_ctx, bal.item_code, warehouse, storage,
                            flt(b["qty"]), entries,
                        )
                    )

            else:
                rows.append(
                    _build_row(
                        ste, parent_ctx, bal.item_code, warehouse, storage,
                        flt(bal.qty), None,
                    )
                )
        except Exception:
            frappe.log_error(
                title="Fetch Stock: skipped item",
                message=f"item={bal.item_code} wh={warehouse} storage={storage}\n"
                + frappe.get_traceback(),
            )
            continue

    return rows


def _build_row(ste, parent_ctx, item_code, warehouse, storage, qty, entries):
    """Build a fully-populated Stock Entry Detail row dict, like a manual add."""
    args = frappe._dict(
        {
            "item_code": item_code,
            "warehouse": warehouse,
            "qty": qty,
            "company": ste.company,
        }
    )
    ret = ste.get_item_details(args) or frappe._dict()

    row = dict(ret)
    row.update(
        {
            "item_code": item_code,
            "s_warehouse": warehouse,
            "storage": storage,
            "qty": qty,
            "transfer_qty": flt(qty) * flt(ret.get("conversion_factor") or 1),
        }
    )

    if entries:
        bundle = _make_bundle(parent_ctx, item_code, warehouse, storage, entries)
        if bundle:
            row["serial_and_batch_bundle"] = bundle
            row["use_serial_batch_fields"] = 0

    return row


def _make_bundle(parent_ctx, item_code, warehouse, storage, entries):
    """Create a draft Serial & Batch Bundle for the row (same path as the
    Pick Serial/Batch dialog), then stamp the Storage dimension on it."""
    from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
        add_serial_batch_ledgers,
    )

    child_row = frappe._dict(
        {
            "doctype": "Stock Entry Detail",
            "parenttype": "Stock Entry",
            "name": "new-fetch-row",
            "item_code": item_code,
            "warehouse": warehouse,
            "s_warehouse": warehouse,
            "t_warehouse": None,
            "is_rejected": 0,
            "serial_and_batch_bundle": None,
        }
    )

    # Suppress the per-bundle "created" alert (would fire once per row).
    prev_mute = frappe.flags.mute_messages
    frappe.flags.mute_messages = True
    try:
        sb_doc = add_serial_batch_ledgers(
            entries, child_row, parent_ctx, warehouse, do_not_save=False
        )
    finally:
        frappe.flags.mute_messages = prev_mute

    if not sb_doc:
        return None

    # Stamp the Storage dimension now so it is visible on the draft bundle;
    # the SLE hook also copies it from the row at submit.
    set_bundle_storage(sb_doc.name, storage)
    return sb_doc.name
