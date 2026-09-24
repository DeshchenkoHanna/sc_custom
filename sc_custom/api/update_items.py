"""Update Item Names — refresh the item name of the item rows from the Item master.

Used by the "Tools > Update Item Names" button on draft Material Request, Request for
Quotation, Purchase Order and Stock Entry (FEAT-186). The rows copy ``item_name`` once,
when the item code is picked, so a later rename of the Item never reaches existing
drafts.

Follows the flow of BOM "Update Cost" with ``save=false``: the server only computes the
changes, the client writes them into the open form and the user saves. Nothing is
written to the database here, so the button also works on unsaved documents.

``REFRESH_FIELDS`` maps Item master field -> row field. Only ``item_name`` for now;
``description`` is deliberately excluded because it is often edited by hand on the row.
"""

import json

import frappe
from frappe import _

SUPPORTED_DOCTYPES = ("Material Request", "Request for Quotation", "Purchase Order", "Stock Entry")
REFRESH_FIELDS = {"item_name": "item_name"}


@frappe.whitelist()
def get_item_updates(doctype, items):
	"""Return the row values that differ from the Item master.

	``items``: list of ``{name, idx, item_code, <row fields>}`` taken from the open form.
	Result: list of ``{name, idx, item_code, fieldname, old, new}``, one per changed value.
	"""
	if doctype not in SUPPORTED_DOCTYPES:
		frappe.throw(_("Update Item Names is not available for {0}").format(_(doctype)))
	frappe.has_permission("Item", "read", throw=True)

	if isinstance(items, str):
		items = json.loads(items)

	item_codes = {row.get("item_code") for row in items if row.get("item_code")}
	if not item_codes:
		return []

	master = {
		d.name: d
		for d in frappe.get_all(
			"Item",
			filters={"name": ("in", list(item_codes))},
			fields=["name", *REFRESH_FIELDS.keys()],
		)
	}

	changes = []
	for row in items:
		item = master.get(row.get("item_code"))
		if not item:
			continue
		for item_field, row_field in REFRESH_FIELDS.items():
			new_value = item.get(item_field) or ""
			old_value = row.get(row_field) or ""
			if new_value != old_value:
				changes.append(
					{
						"name": row.get("name"),
						"idx": row.get("idx"),
						"item_code": row.get("item_code"),
						"fieldname": row_field,
						"old": old_value,
						"new": new_value,
					}
				)
	return changes
