"""
Purchase Receipt Validations for SC Custom
"""

import frappe
from frappe import _
from frappe.utils import getdate


def validate_purchase_receipt(doc, method=None):
    """
    Validate Purchase Receipt items

    - storage field (Target Storage) is mandatory for all items
      Note: In Purchase Receipt Item, 'storage' field represents target storage

    NOTE: Validation only applies to documents with posting_date >= 2026-01-01
    """
    # Only validate documents from 01.01.2026 onwards
    if getdate(doc.posting_date) < getdate("2026-01-01"):
        return

    if not doc.items:
        return

    for item in doc.items:
        # Validate 'storage' field (target storage) is mandatory
        # Note: In Purchase Receipt Item, 'storage' represents target storage
        if not item.storage:
            frappe.throw(
                _("Row #{0}: Target Storage field is mandatory for Purchase Receipt").format(
                    item.idx
                ),
                title=_("Missing Target Storage")
            )
