"""
Patch: Create storage fields added after initial release

Adds storage fields to Work Order, Serial No, Serial and Batch Bundle,
Serial and Batch Entry, Stock Reservation Entry, and Subcontracting Order.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    custom_fields = {
        "Work Order": [
            {
                "fieldname": "wip_storage",
                "label": "Work In Progress Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "wip_warehouse",
                "translatable": 0
            },
            {
                "fieldname": "fg_storage",
                "label": "Target Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "fg_warehouse",
                "translatable": 0
            }
        ],
        "Serial No": [
            {
                "fieldname": "storage",
                "label": "Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "warehouse",
                "read_only": 1,
                "translatable": 0
            }
        ],
        "Serial and Batch Bundle": [
            {
                "fieldname": "storage",
                "label": "Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "warehouse",
                "reqd": 0,
                "translatable": 0
            }
        ],
        "Serial and Batch Entry": [
            {
                "fieldname": "storage",
                "label": "Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "warehouse",
                "translatable": 0
            }
        ],
        "Stock Reservation Entry": [
            {
                "fieldname": "storage",
                "label": "Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "warehouse",
                "translatable": 0
            }
        ],
        "Subcontracting Order": [
            {
                "fieldname": "supplier_storage",
                "label": "Supplier Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "supplier_warehouse",
                "translatable": 0
            }
        ]
    }

    create_custom_fields(custom_fields, update=True)
    print("SC Custom: Storage fields v2 created successfully")
