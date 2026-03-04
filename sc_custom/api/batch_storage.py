"""
Batch Stock Levels with Storage breakdown
"""

import frappe
from frappe.query_builder.functions import Sum


@frappe.whitelist()
def get_batch_qty_by_storage(batch_no, item_code):
    """Returns batch stock levels grouped by warehouse + storage.

    Uses SLE which already has the storage field from Inventory Dimension.
    """
    sle = frappe.qb.DocType("Stock Ledger Entry")
    batch_table = frappe.qb.DocType("Batch")
    batch_ledger = frappe.qb.DocType("Serial and Batch Entry")

    # Modern path: SLE → Serial and Batch Entry
    modern_query = (
        frappe.qb.from_(sle)
        .inner_join(batch_ledger)
        .on(sle.serial_and_batch_bundle == batch_ledger.parent)
        .inner_join(batch_table)
        .on(batch_ledger.batch_no == batch_table.name)
        .select(
            batch_ledger.batch_no,
            batch_ledger.warehouse,
            sle.storage,
            Sum(batch_ledger.qty).as_("qty"),
        )
        .where(sle.is_cancelled == 0)
        .where(batch_table.disabled == 0)
        .where(batch_ledger.batch_no == batch_no)
        .groupby(batch_ledger.batch_no, batch_ledger.warehouse, sle.storage)
    )

    if item_code:
        modern_query = modern_query.where(sle.item_code == item_code)

    modern_results = modern_query.run(as_dict=True)

    # Legacy path: SLE with batch_no directly
    legacy_query = (
        frappe.qb.from_(sle)
        .inner_join(batch_table)
        .on(sle.batch_no == batch_table.name)
        .select(
            sle.batch_no,
            sle.warehouse,
            sle.storage,
            Sum(sle.actual_qty).as_("qty"),
        )
        .where(sle.is_cancelled == 0)
        .where(sle.batch_no.isnotnull())
        .where(batch_table.disabled == 0)
        .where(sle.batch_no == batch_no)
        .groupby(sle.batch_no, sle.warehouse, sle.storage)
    )

    if item_code:
        legacy_query = legacy_query.where(sle.item_code == item_code)

    legacy_results = legacy_query.run(as_dict=True)

    # Merge results using (batch_no, warehouse, storage) as key
    merged = {}
    for row in modern_results:
        key = (row.batch_no, row.warehouse, row.storage)
        merged[key] = merged.get(key, 0) + (row.qty or 0)

    for row in legacy_results:
        key = (row.batch_no, row.warehouse, row.storage)
        merged[key] = merged.get(key, 0) + (row.qty or 0)

    # Compute warehouse-level totals to match standard erpnext behavior:
    # if a warehouse nets to zero, hide all its storage rows.
    warehouse_totals = {}
    for (bn, wh, st), qty in merged.items():
        warehouse_totals[wh] = warehouse_totals.get(wh, 0) + qty

    result = []
    for (batch_no, warehouse, storage), qty in merged.items():
        if warehouse_totals.get(warehouse, 0) == 0:
            continue
        if qty == 0:
            continue
        result.append({
            "batch_no": batch_no,
            "warehouse": warehouse,
            "storage": storage or "",
            "qty": qty,
        })

    result.sort(key=lambda x: (x["warehouse"], x["storage"] or ""))
    return result
