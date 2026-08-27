"""Show each Material Request line's default supplier before a PO is created.

The restored "Create > Purchase Order" supplier flow (see api/material_request.py)
silently drops lines whose item has no matching Item Default supplier. These hooks
fill two read-only grid columns (custom_default_supplier, custom_supplier_part_no)
so users can spot incomplete item masters in advance, and warn about them on submit.
"""

import frappe
from frappe import _


def get_supplier_info_map(item_codes, company=None):
	"""Map item_code -> {default_supplier, supplier_part_no}.

	default_supplier comes from Item Default; the PO supplier filter matches it across
	all companies, so any filled row qualifies — a row of the given company just wins
	when several exist. supplier_part_no is the Item Supplier row of that supplier.
	"""
	item_codes = [ic for ic in set(item_codes or []) if ic]
	if not item_codes:
		return {}

	supplier_map = {}
	for d in frappe.get_all(
		"Item Default",
		filters={"parent": ["in", item_codes], "parenttype": "Item"},
		fields=["parent", "company", "default_supplier"],
	):
		if d.default_supplier and (d.parent not in supplier_map or d.company == company):
			supplier_map[d.parent] = d.default_supplier

	part_no_map = {
		(p.parent, p.supplier): p.supplier_part_no
		for p in frappe.get_all(
			"Item Supplier",
			filters={"parent": ["in", item_codes], "parenttype": "Item"},
			fields=["parent", "supplier", "supplier_part_no"],
		)
	}

	return {
		item_code: {
			"default_supplier": supplier,
			"supplier_part_no": part_no_map.get((item_code, supplier)),
		}
		for item_code, supplier in supplier_map.items()
	}


def set_default_supplier_info(doc, method=None):
	"""before_validate: refresh the info columns from the item master on every save.

	Runs in the submit cycle too, so the columns and the submit warning always reflect
	the current Item Default data even after the item master was fixed.
	"""
	info = {}
	if doc.material_request_type == "Purchase":
		info = get_supplier_info_map([d.item_code for d in doc.get("items", [])], doc.company)

	for d in doc.get("items", []):
		row_info = info.get(d.item_code) or {}
		d.custom_default_supplier = row_info.get("default_supplier")
		d.custom_supplier_part_no = row_info.get("supplier_part_no")


def warn_missing_default_supplier(doc, method=None):
	"""before_submit: non-blocking warning listing lines without a default supplier."""
	if doc.material_request_type != "Purchase":
		return

	missing = [d for d in doc.get("items", []) if d.item_code and not d.custom_default_supplier]
	if not missing:
		return

	rows = "<br>".join(_("Row {0}: {1}").format(d.idx, frappe.bold(d.item_code)) for d in missing)
	frappe.msgprint(
		_(
			"The following items have no Default Supplier in the Item master and will be"
			" skipped when a Purchase Order is created for a specific supplier:"
		)
		+ "<br>"
		+ rows,
		title=_("Missing Default Supplier"),
		indicator="orange",
	)
