"""
Stock Entry Validations for SC Custom
"""

import frappe
from frappe import _
from frappe.utils import getdate


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
        "Material Transfer for Manufacture",
        "Material Consumption for Manufacture",
        "Manufacture"
    ]
    if doc.purpose not in supported_purposes:
        return

    # Get default storage values from Manufacturing Settings
    default_wip_storage = frappe.db.get_single_value("Manufacturing Settings", "default_wip_storage")
    default_fg_storage = frappe.db.get_single_value("Manufacturing Settings", "default_fg_storage")

    if doc.purpose == "Material Transfer for Manufacture":
        if doc.pick_list:
            # Get Pick List items if Stock Entry is created from Pick List
            pick_list_items = frappe.get_all(
                "Pick List Item",
                filters={"parent": doc.pick_list},
                fields=["item_code", "warehouse", "picked_qty", "storage"],
                order_by="idx"
            )

            # Set storage for each Stock Entry Detail item
            for idx, item in enumerate(doc.items):
                # Set source storage from Pick List (if available and matches)
                if idx < len(pick_list_items):
                    pl_item = pick_list_items[idx]
                    if not item.storage and pl_item.storage and item.s_warehouse:
                        item.storage = pl_item.storage

                # Set target storage from Manufacturing Settings default_wip_storage
                if not item.to_storage and item.t_warehouse and default_wip_storage:
                    item.to_storage = default_wip_storage

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

                # Set target storage from Manufacturing Settings default_wip_storage
                if not item.to_storage and item.t_warehouse and default_wip_storage:
                    item.to_storage = default_wip_storage

    elif doc.purpose in ["Material Consumption for Manufacture", "Manufacture"]:
        # For Material Consumption and Manufacture (Finish):
        # - Raw materials come FROM WIP warehouse -> use default_wip_storage
        # - Finished goods go TO FG warehouse -> use default_fg_storage
        for item in doc.items:
            is_finished = getattr(item, 'is_finished_item', 0)

            if is_finished:
                # Finished item: target storage = default_fg_storage
                if not item.to_storage and item.t_warehouse and default_fg_storage:
                    item.to_storage = default_fg_storage
            else:
                # Raw material: source storage = default_wip_storage
                if not item.storage and item.s_warehouse and default_wip_storage:
                    item.storage = default_wip_storage


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
