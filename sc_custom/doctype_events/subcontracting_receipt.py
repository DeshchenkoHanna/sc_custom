"""
Subcontracting Receipt events for SC Custom
"""

import types

import frappe
from frappe import _
from frappe.utils import getdate


def validate_subcontracting_receipt(doc, method=None):
    """Validate handler:
    1. Populate storage / batch_no / serial_no on supplied_items from upstream sources.
    2. Validate that storage is set on items and supplied_items (gated by posting_date)."""
    _populate_supplied_items_storage(doc)
    validate_storage_fields_scr(doc)


def _populate_supplied_items_storage(doc):
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

    # Tracked items (with batch/serial) — fill storage precisely from STE SABB entries.
    if sabb_data:
        _populate_from_sabb(doc, sabb_data)

    # Non-tracked items (no batch/serial) and any tracked items SABB lookup missed —
    # fall back to SCO.supplier_storage. The `if not item.storage` guard inside
    # _populate_from_sco prevents overwriting values already set by _populate_from_sabb.
    _populate_from_sco(doc, sco_name)


def validate_storage_fields_scr(doc):
    """Throw a clear row-level error when storage is missing on items or supplied_items.

    Only enforced for documents with posting_date >= 2026-01-01 (mirrors the gate used
    by the SABB-level validate hook and the Stock Entry storage validations).
    """
    if getdate(doc.posting_date) < getdate("2026-01-01"):
        return

    for item in doc.items or []:
        if not item.storage:
            frappe.throw(
                _("Row #{0}: Storage is mandatory for item {1}").format(
                    item.idx, frappe.bold(item.item_code)
                ),
                title=_("Missing Storage"),
            )

    for sup in doc.supplied_items or []:
        if not sup.storage:
            frappe.throw(
                _("Row #{0}: Storage is mandatory for supplied item {1}").format(
                    sup.idx, frappe.bold(sup.rm_item_code)
                ),
                title=_("Missing Storage"),
            )


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
    """Copy supplier_storage from SCO to supplied_items where storage is still empty.

    Runs after _populate_from_sabb so it backfills:
    - non-tracked items (no batch/serial — never appear in SABB lookup)
    - any tracked items that SABB lookup didn't cover
    """
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


def on_submit_subcontracting_receipt(doc, method=None):
    """Fallback: set storage on SABBs and entries that didn't get it during creation.

    Mirrors the on_submit hook for Stock Entry. Required because ERPNext's
    SerialBatchCreation.set_serial_batch_entries appends entries without a `storage`
    field, so even when our before_submit patch fills the SABB header, the child
    `Serial and Batch Entry` rows stay empty.
    """
    set_storage_on_bundles_scr(doc)


def set_storage_on_bundles_scr(doc):
    """For each items / supplied_items row with a SABB, ensure both the SABB header
    and its entries have storage populated. Uses db_set / direct SQL so the writes
    bypass the SABB validate hook (which would otherwise refuse to update a
    submitted bundle).
    """
    for table_field in ("items", "supplied_items"):
        for row in doc.get(table_field) or []:
            bundle = row.serial_and_batch_bundle or frappe.db.get_value(
                row.doctype, row.name, "serial_and_batch_bundle"
            )
            if not bundle or not row.storage:
                continue

            current = frappe.db.get_value(
                "Serial and Batch Bundle", bundle, "storage"
            )
            if not current:
                frappe.db.set_value(
                    "Serial and Batch Bundle", bundle, "storage", row.storage
                )

            # Always sync SABE entries — header may have storage but entries may not
            frappe.db.sql(
                """
                UPDATE `tabSerial and Batch Entry`
                SET storage = %(storage)s
                WHERE parent = %(bundle)s AND (storage IS NULL OR storage = '')
                """,
                {"storage": row.storage, "bundle": bundle},
            )
