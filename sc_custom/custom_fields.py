"""
Custom Fields for SC Custom App
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_sc_custom_fields():
    """Create custom fields for SC Custom app"""

    custom_fields = {
        "Item": [
            {
                "fieldname": "custom_do_not_explode_default",
                "label": "Do Not Explode by Default",
                "fieldtype": "Check",
                "default": "0",
                "insert_after": "default_bom",
                "description": "When checked, 'Do Not Explode' will be automatically set when this item is added to a BOM",
                "translatable": 0
            },
            {
                "fieldname": "custom_used_in_boms_tab",
                "label": "Used In BOMs",
                "fieldtype": "Tab Break",
                "insert_after": "total_projected_qty",
                "depends_on": "eval:doc.include_item_in_manufacturing",
                "translatable": 0
            },
            {
                "fieldname": "custom_used_in_boms_section",
                "fieldtype": "Section Break",
                "insert_after": "custom_used_in_boms_tab",
                "translatable": 0
            },
            {
                "fieldname": "custom_used_in_boms_html",
                "fieldtype": "HTML",
                "insert_after": "custom_used_in_boms_section",
                "translatable": 0
            },
        ],
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
        "Company": [
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
            },
            {
                "fieldname": "custom_bom_comments",
                "label": "BOM Comments",
                "fieldtype": "Text Editor",
                "insert_after": "stock_uom",
                "allow_on_submit": 1,
                "translatable": 0
            }
        ],
        "BOM": [
            {
                "fieldname": "custom_section_break_rnvev",
                "label": "Production Comments",
                "fieldtype": "Section Break",
                "insert_after": "project",
                "translatable": 0
            },
            {
                "fieldname": "custom_wo_comments",
                "fieldtype": "HTML",
                "insert_after": "custom_section_break_rnvev",
                "read_only": 1,
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


def execute():
    """Execute field creation"""
    create_sc_custom_fields()
