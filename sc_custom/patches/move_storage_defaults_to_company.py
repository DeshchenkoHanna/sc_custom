"""
Patch: Move default storage fields from Manufacturing Settings to Company.

In ERPNext v16, default warehouse fields (WIP, FG, Scrap) moved from
Manufacturing Settings to Company. This patch moves our custom storage
fields to match.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    # 1. Create new custom fields on Company
    custom_fields = {
        "Company": [
            {
                "fieldname": "default_wip_storage",
                "label": "Default Work In Progress Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "default_wip_warehouse",
                "translatable": 0,
            },
            {
                "fieldname": "default_fg_storage",
                "label": "Default Finished Goods Storage",
                "fieldtype": "Link",
                "options": "Storage",
                "insert_after": "default_fg_warehouse",
                "translatable": 0,
            },
        ]
    }
    create_custom_fields(custom_fields, update=True)

    # 2. Copy values from Manufacturing Settings to default Company
    old_wip = None
    old_fg = None

    ms_meta = frappe.get_meta("Manufacturing Settings")
    if ms_meta.has_field("default_wip_storage"):
        old_wip = frappe.db.get_single_value("Manufacturing Settings", "default_wip_storage")
    if ms_meta.has_field("default_fg_storage"):
        old_fg = frappe.db.get_single_value("Manufacturing Settings", "default_fg_storage")

    if old_wip or old_fg:
        company = frappe.defaults.get_defaults().company
        if company:
            if old_wip:
                frappe.db.set_value("Company", company, "default_wip_storage", old_wip)
            if old_fg:
                frappe.db.set_value("Company", company, "default_fg_storage", old_fg)
            print(f"SC Custom: Copied default storage values to Company '{company}'")

    # 3. Delete old custom fields from Manufacturing Settings
    for field_name in [
        "Manufacturing Settings-default_wip_storage",
        "Manufacturing Settings-default_fg_storage",
    ]:
        if frappe.db.exists("Custom Field", field_name):
            frappe.delete_doc("Custom Field", field_name, force=True)
            print(f"SC Custom: Deleted old custom field '{field_name}'")

    frappe.db.commit()
    print("SC Custom: Default storage fields moved to Company successfully")
