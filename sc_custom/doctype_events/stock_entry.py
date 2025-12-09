"""
Stock Entry Validations for SC Custom
"""

import frappe
from frappe import _
from frappe.utils import getdate


def validate_stock_entry(doc, method=None):
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

        #Validate 'to_storage' field (destination storage)
        if doc.purpose in ["Material Issue", "Material Consumption for Manufacture"]:
            if not item.to_storage:
                frappe.throw(
                    _("Row #{0}: Target Storage field is mandatory for Stock Entry with purpose '{1}'").format(
                        item.idx, doc.purpose
                    ),
                    title=_("Missing To Storage")
                )
