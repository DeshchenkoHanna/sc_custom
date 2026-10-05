"""Show each Material Request line's default supplier before a PO is created.

The restored "Create > Purchase Order" supplier flow (see api/material_request.py)
silently drops lines whose item has no matching Item Default supplier. These hooks
fill two read-only grid columns (custom_default_supplier, custom_supplier_part_no)
so users can spot incomplete item masters in advance, and warn about them on submit.
"""

import frappe
from frappe import _
from frappe.utils import flt


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


def _get_row_supplier_values(doc):
	"""Yield (row, {custom_default_supplier, custom_supplier_part_no}) with the values the
	item master currently gives for each item row (all None for non-Purchase requests)."""
	info = {}
	if doc.material_request_type == "Purchase":
		info = get_supplier_info_map([d.item_code for d in doc.get("items", [])], doc.company)

	for d in doc.get("items", []):
		row_info = info.get(d.item_code) or {}
		yield d, {
			"custom_default_supplier": row_info.get("default_supplier") or None,
			"custom_supplier_part_no": row_info.get("supplier_part_no") or None,
		}


def set_default_supplier_info(doc, method=None):
	"""before_validate: refresh the info columns from the item master on every save.

	Runs in the submit cycle too, so the columns and the submit warning always reflect
	the current Item Default data even after the item master was fixed.
	"""
	for d, values in _get_row_supplier_values(doc):
		d.update(values)


def refresh_default_supplier_info(doc, method=None):
	"""onload: sync the info columns with the item master when the form opens.

	Covers drafts and submitted requests that are still open for ordering (not Stopped,
	less than 100% ordered). A request saved or submitted before the item master was
	fixed would otherwise show stale values — a submitted one forever, as no save runs
	on it any more. The columns are display-only (PO creation reads Item Default
	itself), so updating them after submit is safe.

	Changed rows are written straight to the database (update_modified=False, so a
	later save does not hit a timestamp mismatch) and the loaded document is updated
	in place, so the form shows exactly what is stored and stays clean.

	getdoc is a GET request and Frappe rolls GET transactions back, hence the explicit
	commit right after our own writes: at this point in the request nothing else has
	been written (core onload handlers only read; View Log / _seen are deferred to
	after the response with their own commit), so only these rows get committed.
	"""
	if doc.material_request_type != "Purchase":
		return
	if doc.docstatus == 1:
		if doc.status == "Stopped" or flt(doc.per_ordered) >= 100:
			return
	elif doc.docstatus != 0:
		return

	changed = False
	for d, values in _get_row_supplier_values(doc):
		if not d.name or d.get("__islocal"):
			continue
		diff = {k: v for k, v in values.items() if (d.get(k) or None) != v}
		if not diff:
			continue
		d.update(diff)
		frappe.db.set_value(d.doctype, d.name, diff, update_modified=False)
		changed = True

	if changed:
		frappe.db.commit()  # nosemgrep: see docstring, GET request would roll back
		frappe.clear_document_cache(doc.doctype, doc.name)


def set_project_on_items(doc, method=None):
	"""before_validate: fill empty item-row Project from the header Project.

	Covers rows created outside the form (Get Items from BOM, API, bulk update),
	which the client script never sees. Only empty rows are filled — overwriting
	a deliberately different row project is a form-only, user-confirmed action.
	"""
	if not doc.get("custom_project"):
		return

	for d in doc.get("items", []):
		if not d.project:
			d.project = doc.custom_project


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
