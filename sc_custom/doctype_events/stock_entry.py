"""
Stock Entry Validations for SC Custom
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today


def get_fifo_storage_for_item(item_code, warehouse, required_qty):
    """
    Get the first available storage location for an item using FIFO.

    Args:
        item_code: Item code to search for
        warehouse: Warehouse where storage is located
        required_qty: Required quantity

    Returns:
        Storage name or None if no storage found
    """
    if not item_code or not warehouse or not required_qty:
        return None

    # Query Stock Ledger Entry to get storage with available qty, ordered by FIFO
    storage_data = frappe.db.sql("""
        SELECT
            sle.storage,
            SUM(sle.actual_qty) as available_qty
        FROM `tabStock Ledger Entry` sle
        WHERE
            sle.item_code = %(item_code)s
            AND sle.warehouse = %(warehouse)s
            AND sle.storage IS NOT NULL
            AND sle.storage != ''
            AND sle.is_cancelled = 0
            AND sle.docstatus < 2
        GROUP BY sle.storage
        HAVING available_qty > 0
        ORDER BY MIN(sle.posting_date), MIN(sle.posting_time), MIN(sle.creation)
        LIMIT 1
    """, {
        'item_code': item_code,
        'warehouse': warehouse
    }, as_dict=True)

    if storage_data:
        return storage_data[0].get('storage')

    return None


def validate_stock_entry(doc, method=None):
    """Main validation handler for Stock Entry"""
    set_default_storage(doc)
    validate_storage_fields(doc)
    validate_batch_serial_storage(doc)


def before_submit_stock_entry(doc, method=None):
    """Monkey-patch create_serial_batch_bundle to inject storage into auto-created SABBs.

    Standard ERPNext creates SABBs on submit (via make_bundle_using_old_serial_batch_fields)
    but doesn't know about storage. We wrap create_serial_batch_bundle so that
    storage is included in the bundle_details dict before SABB creation.
    """
    _patch_create_serial_batch_bundle(doc)


def on_submit_stock_entry(doc, method=None):
    """Fallback: set storage on any SABBs that still don't have it after submit."""
    set_storage_on_bundles(doc)


def set_default_storage(doc):
    """
    Set default storage locations from Manufacturing Settings
    Similar to how Work Order sets default wip_warehouse

    For Material Transfer for Manufacture:
    - Copy source storage from Pick List items
    - Set target storage from Manufacturing Settings default_wip_storage

    For Material Consumption for Manufacture:
    - Set source storage from Manufacturing Settings default_wip_storage (consuming from WIP)
    - Set target storage from Manufacturing Settings default_fg_storage (for finished goods)

    For Manufacture (Finish):
    - Raw materials: source storage = default_wip_storage
    - Finished goods: target storage = default_fg_storage
    """
    supported_purposes = [
        "Material Transfer",
        "Material Transfer for Manufacture",
        "Material Consumption for Manufacture",
        "Manufacture"
    ]
    if doc.purpose not in supported_purposes:
        return

    # Get default storage values from Manufacturing Settings
    default_wip_storage = frappe.db.get_single_value("Manufacturing Settings", "default_wip_storage")
    default_fg_storage = frappe.db.get_single_value("Manufacturing Settings", "default_fg_storage")

    # If linked to a Work Order, prefer its storage values over Manufacturing Settings defaults
    wo_wip_storage = None
    wo_fg_storage = None
    if doc.work_order:
        wo_wip_storage, wo_fg_storage = frappe.db.get_value(
            "Work Order", doc.work_order, ["wip_storage", "fg_storage"]
        ) or (None, None)

    wip_storage = wo_wip_storage or default_wip_storage
    fg_storage = wo_fg_storage or default_fg_storage

    if doc.purpose in ["Material Transfer", "Material Transfer for Manufacture"]:
        if doc.pick_list:
            # Get Pick List items if Stock Entry is created from Pick List
            pick_list_items = frappe.get_all(
                "Pick List Item",
                filters={"parent": doc.pick_list},
                fields=["item_code", "warehouse", "picked_qty", "storage", "serial_and_batch_bundle"],
                order_by="idx"
            )

            # Set storage and serial numbers for each Stock Entry Detail item
            for idx, item in enumerate(doc.items):
                if idx < len(pick_list_items):
                    pl_item = pick_list_items[idx]

                    # Set source storage from Pick List (if available and matches)
                    if not item.storage and pl_item.storage and item.s_warehouse:
                        item.storage = pl_item.storage

                    # Inherit serial/batch from PL SABB only on first creation.
                    # On subsequent saves the user may have changed storage/batch/serial
                    # manually — don't overwrite their changes.
                    if doc.get("__islocal") and pl_item.serial_and_batch_bundle:
                        if not item.serial_no:
                            serial_nos = frappe.get_all(
                                "Serial and Batch Entry",
                                filters={"parent": pl_item.serial_and_batch_bundle, "serial_no": ("is", "set")},
                                pluck="serial_no",
                            )
                            if serial_nos:
                                item.serial_no = "\n".join(serial_nos)
                                item.use_serial_batch_fields = 1

                        if not item.batch_no:
                            sabb_source = item.serial_and_batch_bundle or pl_item.serial_and_batch_bundle
                            batch_no = frappe.db.get_value(
                                "Serial and Batch Entry",
                                {"parent": sabb_source, "batch_no": ("is", "set")},
                                "batch_no",
                            )
                            if batch_no:
                                item.batch_no = batch_no
                                item.use_serial_batch_fields = 1

                # Set target storage: WO wip_storage > Manufacturing Settings default
                if not item.to_storage and item.t_warehouse and wip_storage:
                    item.to_storage = wip_storage

        elif doc.work_order:
            # Get storage from available stock (FIFO) for Work Order items
            for item in doc.items:
                # Set source storage from available stock using FIFO
                if not item.storage and item.s_warehouse and item.item_code:
                    storage = get_fifo_storage_for_item(
                        item.item_code,
                        item.s_warehouse,
                        item.qty or item.transfer_qty or 0
                    )
                    if storage:
                        item.storage = storage

                # Set target storage: WO wip_storage > Manufacturing Settings default
                if not item.to_storage and item.t_warehouse and wip_storage:
                    item.to_storage = wip_storage

    elif doc.purpose in ["Material Consumption for Manufacture", "Manufacture"]:
        # On first creation, try to inherit storage/batch/serial from
        # the inward side of "Material Transfer for Manufacture" STEs for this WO
        transfer_items = {}
        if doc.get("__islocal") and doc.work_order:
            transfer_items = _get_transfer_inward_items(doc.work_order)

        for item in doc.items:
            is_finished = getattr(item, 'is_finished_item', 0)

            if is_finished:
                # Finished item: target storage from WO fg_storage > Manufacturing Settings
                if not item.to_storage and item.t_warehouse and fg_storage:
                    item.to_storage = fg_storage
            else:
                t_item = transfer_items.get(item.item_code)

                # Storage: transfer STE to_storage > WO wip_storage > Manufacturing Settings
                if not item.storage and item.s_warehouse:
                    if t_item and t_item.get("to_storage"):
                        item.storage = t_item["to_storage"]
                    elif wip_storage:
                        item.storage = wip_storage

                # Batch/serial from transfer STE (only on first creation)
                if doc.get("__islocal") and t_item:
                    if not item.batch_no and t_item.get("batch_no"):
                        item.batch_no = t_item["batch_no"]
                        item.use_serial_batch_fields = 1
                    if not item.serial_no and t_item.get("serial_nos"):
                        item.serial_no = "\n".join(t_item["serial_nos"])
                        item.use_serial_batch_fields = 1


@frappe.whitelist()
def get_transfer_inward_items(work_order):
    """Whitelisted wrapper for _get_transfer_inward_items."""
    return _get_transfer_inward_items(work_order)


def _get_transfer_inward_items(work_order):
    """Get inward (target) data from submitted Material Transfer for Manufacture STEs.

    Returns dict keyed by item_code with to_storage, batch_no, serial_nos.
    If multiple transfers exist for the same item, the latest STE wins.
    """
    transfer_stes = frappe.get_all(
        "Stock Entry",
        filters={
            "work_order": work_order,
            "purpose": "Material Transfer for Manufacture",
            "docstatus": 1,
        },
        fields=["name"],
        order_by="creation asc",
    )

    if not transfer_stes:
        return {}

    result = {}
    for ste in transfer_stes:
        items = frappe.get_all(
            "Stock Entry Detail",
            filters={"parent": ste.name},
            fields=[
                "item_code", "to_storage", "batch_no", "serial_no",
                "serial_and_batch_bundle",
            ],
        )
        for item in items:
            batch_no = item.batch_no
            serial_nos = []

            # Get batch/serial from SABB if not on the row
            if item.serial_and_batch_bundle:
                if not batch_no:
                    batch_no = frappe.db.get_value(
                        "Serial and Batch Entry",
                        {"parent": item.serial_and_batch_bundle, "batch_no": ("is", "set")},
                        "batch_no",
                    )
                if not serial_nos:
                    serial_nos = frappe.get_all(
                        "Serial and Batch Entry",
                        filters={"parent": item.serial_and_batch_bundle, "serial_no": ("is", "set")},
                        pluck="serial_no",
                    )

            if item.serial_no and not serial_nos:
                serial_nos = [s.strip() for s in item.serial_no.split("\n") if s.strip()]

            result[item.item_code] = {
                "to_storage": item.to_storage or "",
                "batch_no": batch_no or "",
                "serial_nos": serial_nos,
            }

    return result


def _patch_create_serial_batch_bundle(doc):
    """Wrap doc.create_serial_batch_bundle to inject storage into bundle_details."""
    original_create = doc.create_serial_batch_bundle.__func__

    def patched_create(self, bundle_details, row):
        # Inject storage based on direction
        if row.s_warehouse and row.storage:
            bundle_details["storage"] = row.storage
        elif row.t_warehouse and row.to_storage:
            bundle_details["storage"] = row.to_storage
        return original_create(self, bundle_details, row)

    import types
    doc.create_serial_batch_bundle = types.MethodType(patched_create, doc)


def set_storage_on_bundles(doc):
    """Set storage on SABBs that were auto-created without it.

    For each STE item that has a SABB but the SABB has no storage,
    set storage from the row:
    - Outward (s_warehouse): use item.storage
    - Inward (t_warehouse only): use item.to_storage
    """
    if not doc.items:
        return

    for item in doc.items:
        if not item.serial_and_batch_bundle:
            continue

        # Determine which storage to use based on direction
        if item.s_warehouse:
            storage = item.storage
        else:
            storage = item.to_storage

        if not storage:
            continue

        # Check if SABB already has storage set
        current_storage = frappe.db.get_value(
            "Serial and Batch Bundle", item.serial_and_batch_bundle, "storage"
        )
        if current_storage:
            continue

        # Set storage on SABB and its entries
        frappe.db.set_value(
            "Serial and Batch Bundle", item.serial_and_batch_bundle, "storage", storage
        )
        frappe.db.sql("""
            UPDATE `tabSerial and Batch Entry`
            SET storage = %(storage)s
            WHERE parent = %(bundle)s
        """, {"storage": storage, "bundle": item.serial_and_batch_bundle})


def validate_storage_fields(doc):
    """
    Validate Stock Entry Detail items based on Stock Entry purpose

    - storage field is mandatory if purpose != 'Material Receipt'
    - to_storage field is mandatory if purpose != 'Material Issue'
    - For Repack/Manufacture: If "Is Finished Item" is checked, Target Storage is mandatory; otherwise Source Storage is mandatory

    NOTE: Validation only applies to documents with posting_date >= 2026-01-01
    """
    # Only validate documents from 01.01.2026 onwards
    if getdate(doc.posting_date) < getdate("2026-01-01"):
        return

    if not doc.items:
        return

    for item in doc.items:
        # Special validation for Repack and Manufacture
        if doc.purpose in ["Repack", "Manufacture"]:
            # Check if item is marked as finished item
            is_finished = getattr(item, 'is_finished_item', 0)

            if is_finished:
                # Finished items require Target Storage (to_storage)
                if not item.to_storage:
                    frappe.throw(
                        _("Row #{0}: Target Storage is mandatory for Finished Items in {1}").format(
                            item.idx, doc.purpose
                        ),
                        title=_("Missing Target Storage")
                    )
            else:
                # Raw materials/inputs require Source Storage (storage)
                if not item.storage:
                    frappe.throw(
                        _("Row #{0}: Source Storage is mandatory for raw materials in {1}").format(
                            item.idx, doc.purpose
                        ),
                        title=_("Missing Source Storage")
                    )
            # Skip other validations for Repack/Manufacture
            continue

        # Validate 'storage' field (from_storage/source storage)
        if doc.purpose != "Material Receipt":
            if not item.storage:
                frappe.throw(
                    _("Row #{0}: Source Storage field is mandatory for Stock Entry with purpose '{1}'").format(
                        item.idx, doc.purpose
                    ),
                    title=_("Missing Storage")
                )

        # Validate 'to_storage' field (destination storage)
        if doc.purpose == "Material Receipt":
            # Material Receipt: target storage is mandatory (receiving goods into storage)
            if not item.to_storage:
                frappe.throw(
                    _("Row #{0}: Target Storage field is mandatory for Stock Entry with purpose '{1}'").format(
                        item.idx, doc.purpose
                    ),
                    title=_("Missing Target Storage")
                )
        elif doc.purpose in ["Material Transfer", "Material Transfer for Manufacture", "Disassemble", "Send to Subcontractor"]:
            if not item.to_storage:
                frappe.throw(
                    _("Row #{0}: Target Storage field is mandatory for Stock Entry with purpose '{1}'").format(
                        item.idx, doc.purpose
                    ),
                    title=_("Missing To Storage")
                )
        # elif doc.purpose in ["Material Issue", "Material Consumption for Manufacture"]:
        #     if not item.to_storage:
        #         frappe.throw(
        #             _("Row #{0}: Target Storage field is mandatory for Stock Entry with purpose '{1}'").format(
        #                 item.idx, doc.purpose
        #             ),
        #             title=_("Missing To Storage")
        #         )


def validate_batch_serial_storage(doc):
    """Validate that batch/serial numbers have sufficient qty in the specified storage.

    Checks outward items (with s_warehouse + storage) to ensure the batch or serial
    actually exists in that storage with enough quantity.
    Only applies to documents with posting_date >= 2026-01-01.
    """
    if getdate(doc.posting_date) < getdate("2026-01-01"):
        return

    if not doc.items:
        return

    for item in doc.items:
        # Only validate outward (source) side
        if not item.s_warehouse or not item.storage:
            continue

        has_batch_no = frappe.get_cached_value("Item", item.item_code, "has_batch_no")
        has_serial_no = frappe.get_cached_value("Item", item.item_code, "has_serial_no")

        if not has_batch_no and not has_serial_no:
            continue

        # Get batch_no from row or from SABB
        batch_no = item.batch_no
        if not batch_no and item.serial_and_batch_bundle and has_batch_no:
            batch_no = frappe.db.get_value(
                "Serial and Batch Entry",
                {"parent": item.serial_and_batch_bundle, "batch_no": ("is", "set")},
                "batch_no",
            )

        # Validate batch + storage
        if has_batch_no and batch_no:
            available = _get_batch_qty_in_storage(
                item.item_code, item.s_warehouse, item.storage, batch_no
            )
            required = flt(item.qty) * flt(item.conversion_factor or 1)
            if available < required:
                frappe.throw(
                    _("Row #{0}: Batch {1} has only {2} qty available in Storage {3} "
                      "(Warehouse: {4}), but {5} is required.").format(
                        item.idx, frappe.bold(batch_no), available,
                        frappe.bold(item.storage), item.s_warehouse, required
                    ),
                    title=_("Insufficient Batch Qty in Storage")
                )

        # Validate serial nos + storage
        if has_serial_no:
            serial_nos = []
            if item.serial_no:
                serial_nos = [s.strip() for s in item.serial_no.split("\n") if s.strip()]
            elif item.serial_and_batch_bundle:
                serial_nos = frappe.get_all(
                    "Serial and Batch Entry",
                    filters={"parent": item.serial_and_batch_bundle, "serial_no": ("is", "set")},
                    pluck="serial_no",
                )

            if serial_nos:
                wrong_storage = _get_serials_not_in_storage(
                    serial_nos, item.s_warehouse, item.storage
                )
                if wrong_storage:
                    frappe.throw(
                        _("Row #{0}: Serial No(s) {1} are not in Storage {2} "
                          "(Warehouse: {3}).").format(
                            item.idx,
                            frappe.bold(", ".join(wrong_storage[:5])),
                            frappe.bold(item.storage),
                            item.s_warehouse,
                        ),
                        title=_("Serial No Not in Storage")
                    )


def _get_batch_qty_in_storage(item_code, warehouse, storage, batch_no):
    """Get available qty for a batch in a specific storage."""
    # From SABB/SABE
    sabb_qty = frappe.db.sql("""
        SELECT IFNULL(SUM(sabe.qty), 0)
        FROM `tabSerial and Batch Entry` sabe
        JOIN `tabSerial and Batch Bundle` sabb ON sabe.parent = sabb.name
        WHERE
            sabb.item_code = %(item_code)s
            AND sabe.warehouse = %(warehouse)s
            AND sabb.storage = %(storage)s
            AND sabe.batch_no = %(batch_no)s
            AND sabb.docstatus = 1
            AND sabb.is_cancelled = 0
            AND sabb.voucher_type != 'Pick List'
    """, {
        "item_code": item_code,
        "warehouse": warehouse,
        "storage": storage,
        "batch_no": batch_no,
    })[0][0] or 0

    # From SLE
    sle_qty = frappe.db.sql("""
        SELECT IFNULL(SUM(sle.actual_qty), 0)
        FROM `tabStock Ledger Entry` sle
        WHERE
            sle.item_code = %(item_code)s
            AND sle.warehouse = %(warehouse)s
            AND sle.storage = %(storage)s
            AND sle.batch_no = %(batch_no)s
            AND sle.is_cancelled = 0
    """, {
        "item_code": item_code,
        "warehouse": warehouse,
        "storage": storage,
        "batch_no": batch_no,
    })[0][0] or 0

    return flt(max(sabb_qty, sle_qty))


def _get_serials_not_in_storage(serial_nos, warehouse, storage):
    """Return serial numbers that are not in the specified warehouse+storage."""
    if not serial_nos:
        return []

    existing = frappe.get_all(
        "Serial No",
        filters={
            "name": ("in", serial_nos),
            "warehouse": warehouse,
            "storage": storage,
        },
        pluck="name",
    )

    return [sn for sn in serial_nos if sn not in existing]


@frappe.whitelist()
def check_ste_pl_differences(ste_name):
    """Compare STE items with their Pick List source and return differences.

    Returns list of dicts with idx and list of changed fields.
    """
    doc = frappe.get_doc("Stock Entry", ste_name)
    if not doc.pick_list:
        return []

    pl_items = frappe.get_all(
        "Pick List Item",
        filters={"parent": doc.pick_list},
        fields=["item_code", "warehouse", "storage", "batch_no", "serial_and_batch_bundle"],
        order_by="idx",
    )

    differences = []
    for idx, item in enumerate(doc.items):
        if idx >= len(pl_items):
            break

        pl = pl_items[idx]
        row_diffs = []

        # Compare storage
        if item.storage and pl.storage and item.storage != pl.storage:
            row_diffs.append(_("Storage: {0} → {1}").format(pl.storage, item.storage))

        # Compare batch_no
        pl_batch = pl.batch_no
        if not pl_batch and pl.serial_and_batch_bundle:
            pl_batch = frappe.db.get_value(
                "Serial and Batch Entry",
                {"parent": pl.serial_and_batch_bundle, "batch_no": ("is", "set")},
                "batch_no",
            )

        ste_batch = item.batch_no
        if not ste_batch and item.serial_and_batch_bundle:
            ste_batch = frappe.db.get_value(
                "Serial and Batch Entry",
                {"parent": item.serial_and_batch_bundle, "batch_no": ("is", "set")},
                "batch_no",
            )

        if ste_batch and pl_batch and ste_batch != pl_batch:
            row_diffs.append(_("Batch No: {0} → {1}").format(pl_batch, ste_batch))

        # Compare serial nos
        pl_serials = set()
        if pl.serial_and_batch_bundle:
            pl_serials = set(frappe.get_all(
                "Serial and Batch Entry",
                filters={"parent": pl.serial_and_batch_bundle, "serial_no": ("is", "set")},
                pluck="serial_no",
            ))

        ste_serials = set()
        if item.serial_no:
            ste_serials = {s.strip() for s in item.serial_no.split("\n") if s.strip()}
        elif item.serial_and_batch_bundle:
            ste_serials = set(frappe.get_all(
                "Serial and Batch Entry",
                filters={"parent": item.serial_and_batch_bundle, "serial_no": ("is", "set")},
                pluck="serial_no",
            ))

        if pl_serials and ste_serials and pl_serials != ste_serials:
            added = ste_serials - pl_serials
            removed = pl_serials - ste_serials
            parts = []
            if removed:
                parts.append(_("removed: {0}").format(", ".join(list(removed)[:3])))
            if added:
                parts.append(_("added: {0}").format(", ".join(list(added)[:3])))
            row_diffs.append(_("Serial Nos: {0}").format("; ".join(parts)))

        if row_diffs:
            differences.append({
                "idx": item.idx,
                "item_code": item.item_code,
                "diffs": row_diffs,
            })

    return differences
