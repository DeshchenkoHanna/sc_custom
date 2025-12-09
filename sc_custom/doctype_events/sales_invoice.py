"""
Sales Invoice Validations for SC Custom
"""

import frappe
from frappe import _
from frappe.utils import getdate


def validate_sales_invoice(doc, method=None):
    """
    Validate Sales Invoice items

    - storage field (Source Warehouse) is mandatory for all items when "Update Stock" is checked

    NOTE: Validation only applies to documents with posting_date >= 2026-01-01
    """
    # Only validate documents from 01.01.2026 onwards
    if getdate(doc.posting_date) < getdate("2026-01-01"):
        return

    # Only validate if "Update Stock" is enabled
    if not doc.update_stock:
        return

    if not doc.items:
        return

    for item in doc.items:
        # Validate 'storage' field (source warehouse) is mandatory when update stock is checked
        if not item.storage:
            frappe.throw(
                _("Row #{0}: Source Storage field is mandatory for Sales Invoice when 'Update Stock' is enabled").format(
                    item.idx
                ),
                title=_("Missing Source Storage")
            )
