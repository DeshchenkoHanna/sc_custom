import frappe
from erpnext.stock.doctype.material_request.material_request import (
	make_purchase_order,
	make_purchase_order_based_on_supplier,
)


@frappe.whitelist()
def make_purchase_order_with_supplier(source_name, target_doc=None):
	"""Restore the v15 "Create > Purchase Order" behaviour for Material Requests.

	ERPNext v16 (PR #53391) removed the supplier prompt and its item filtering. This
	wrapper is ``open_mapped_doc``-compatible: ``make_mapped_doc`` calls the method with
	only ``source_name`` and exposes the supplier via ``frappe.flags.args`` (it never
	passes the ``args`` parameter that ``make_purchase_order_based_on_supplier`` expects).

	If a supplier is chosen, build a PO with only the items whose Item Default supplier
	matches; with no supplier, fall back to the standard all-items mapping.
	"""
	args = frappe.flags.args or frappe._dict()
	default_supplier = args.get("default_supplier")

	if default_supplier:
		doc = make_purchase_order_based_on_supplier(
			source_name, target_doc, {"supplier": default_supplier}
		)
		# erpnext drops the non-matching rows via Document.set("items", ...), which keeps each
		# surviving row's original idx (append only assigns idx when it is falsy). Renumber so the
		# PO rows are sequential 1..N instead of inheriting the Material Request row positions.
		for idx, row in enumerate(doc.get("items") or [], start=1):
			row.idx = idx
		return doc

	return make_purchase_order(source_name, target_doc)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_default_supplier_query(doctype, txt, searchfield, start, page_len, filters):
	"""Limit the supplier prompt to the default suppliers of this Material Request's items.

	Restored verbatim from ERPNext v15 (removed in v16 PR #53391).
	"""
	doc = frappe.get_doc("Material Request", filters.get("doc"))
	item_list = [d.item_code for d in doc.items]

	supplier = frappe.qb.DocType("Supplier")
	item_default = frappe.qb.DocType("Item Default")
	query = (
		frappe.qb.from_(supplier)
		.left_join(item_default)
		.on(supplier.name == item_default.default_supplier)
		.select(item_default.default_supplier)
		.distinct()
		.where(
			(item_default.parent.isin(item_list))
			& (item_default.default_supplier.notnull())
			& (supplier[searchfield].like(f"%{txt}%"))
		)
		.offset(start)
		.limit(page_len)
	)

	meta = frappe.get_meta("Supplier")
	if meta.show_title_field_in_link and meta.title_field:
		query = query.select(supplier[meta.title_field])

	return query.run(as_dict=False)
