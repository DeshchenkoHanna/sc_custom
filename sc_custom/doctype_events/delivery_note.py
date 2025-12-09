"""
Delivery Note Validations for SC Custom
"""

import frappe
from frappe import _
from frappe.utils import getdate


def validate_delivery_note(doc, method=None):
    """
    Validate Delivery Note items

    - storage field (Source Warehouse) is mandatory for all items

    NOTE: Validation only applies to documents with posting_date >= 2026-01-01
    """
    # Only validate documents from 01.01.2026 onwards
    if getdate(doc.posting_date) < getdate("2026-01-01"):
        return

    if not doc.items:
        return

    for item in doc.items:
        # Validate 'storage' field (source warehouse) is mandatory
        if not item.storage:
            frappe.throw(
                _("Row #{0}: Source Storage field is mandatory for Delivery Note").format(
                    item.idx
                ),
                title=_("Missing Source Storage")
            )
