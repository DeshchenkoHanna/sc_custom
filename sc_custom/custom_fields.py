"""
Custom Fields for SC Custom App
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_sc_custom_fields():
    """Create custom fields for SC Custom app"""

    custom_fields = {
        "Pick List Item": [
            {
                "fieldname": "storage",
                "label": "Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "warehouse",
                "translatable": 0
            }
        ],
        "Manufacturing Settings": [
            {
                "fieldname": "default_wip_storage",
                "label": "Default Work In Progress Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "default_wip_warehouse",
                "translatable": 0
            },
            {
                "fieldname": "default_fg_storage",
                "label": "Default Finished Goods Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "default_fg_warehouse",
                "translatable": 0
            }
        ],
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
        ]
    }

    create_custom_fields(custom_fields, update=True)


def execute():
    """Execute field creation"""
    create_sc_custom_fields()
