"""
Subcontracting Receipt events for SC Custom
"""

import types

import frappe


def validate_subcontracting_receipt(doc, method=None):
    """Populate storage, serial_no, batch_no on supplied_items from Send to Subcontractor STE SABBs.
    Falls back to SCO supplier_storage if no SABB data found."""
    sco_name = None
    for item in doc.items or []:
        if item.subcontracting_order:
            sco_name = item.subcontracting_order
            break

    if not sco_name:
        return

    # Fetch inward SABB entries from Send to Subcontractor STEs for this SCO
    sabb_data = frappe.db.sql("""
        SELECT
            sabb.item_code,
            sabe.batch_no,
            sabe.serial_no,
            sabe.storage,
            sabe.qty
        FROM `tabSerial and Batch Entry` sabe
        JOIN `tabSerial and Batch Bundle` sabb ON sabe.parent = sabb.name
        WHERE sabb.voucher_type = 'Stock Entry'
            AND sabb.type_of_transaction = 'Inward'
            AND sabb.docstatus = 1
            AND sabb.is_cancelled = 0
            AND sabb.voucher_no IN (
                SELECT name FROM `tabStock Entry`
                WHERE purpose = 'Send to Subcontractor'
                    AND subcontracting_order = %(sco_name)s
                    AND docstatus = 1
            )
    """, {"sco_name": sco_name}, as_dict=True)

    if sabb_data:
        _populate_from_sabb(doc, sabb_data)
    else:
        _populate_from_sco(doc, sco_name)


def _populate_from_sabb(doc, sabb_data):
    """Populate supplied_items from SABB entries."""
    item_map = {}
    for row in sabb_data:
        item_map.setdefault(row.item_code, []).append(row)

    for item in doc.supplied_items or []:
        entries = item_map.get(item.rm_item_code)
        if not entries:
            continue

        if not item.storage and entries[0].storage:
            item.storage = entries[0].storage

        if not item.batch_no:
            batch = next((e.batch_no for e in entries if e.batch_no), None)
            if batch:
                item.batch_no = batch
                item.use_serial_batch_fields = 1

        if not item.serial_no:
            serial_nos = [e.serial_no for e in entries if e.serial_no]
            if serial_nos:
                item.serial_no = "\n".join(serial_nos)
                item.use_serial_batch_fields = 1


def _populate_from_sco(doc, sco_name):
    """Fallback: copy supplier_storage from SCO to supplied_items."""
    supplier_storage = frappe.db.get_value(
        "Subcontracting Order", sco_name, "supplier_storage"
    )
    if not supplier_storage:
        return

    for item in doc.supplied_items or []:
        if not item.storage:
            item.storage = supplier_storage


def before_submit_subcontracting_receipt(doc, method=None):
    """Patch create_serial_batch_bundle to inject storage into auto-created SABBs."""
    original_create = doc.create_serial_batch_bundle.__func__

    def patched_create(self, bundle_details, row):
        if row.get("storage"):
            bundle_details["storage"] = row.storage
        return original_create(self, bundle_details, row)

    doc.create_serial_batch_bundle = types.MethodType(patched_create, doc)
