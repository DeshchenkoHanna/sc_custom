import frappe
from frappe.utils import sbool


@frappe.whitelist()
def get_used_in_boms(item_code=None, parent=None, is_root=False):
	is_root = sbool(is_root)

	if not item_code and not parent:
		return []

	if is_root:
		return _get_root_boms(item_code)
	elif parent:
		return _get_parent_boms(parent)

	return []


def _get_root_boms(item_code):
	data = frappe.db.sql(
		"""
		SELECT
			bi.parent AS value,
			b.item AS item_code,
			b.item_name,
			bi.qty,
			bi.uom AS stock_uom
		FROM `tabBOM Item` bi
		JOIN `tabBOM` b ON b.name = bi.parent
		WHERE bi.item_code = %(item_code)s
			AND b.docstatus = 1
		ORDER BY b.item, bi.parent
		""",
		{"item_code": item_code},
		as_dict=True,
	)

	bom_names = [d.value for d in data]
	expandable = _get_boms_with_parents(bom_names) if bom_names else set()
	for d in data:
		d.expandable = 1 if d.value in expandable else 0

	return data


def _get_parent_boms(bom_name):
	data = frappe.db.sql(
		"""
		SELECT
			bi.parent AS value,
			b.item AS item_code,
			b.item_name,
			bi.qty,
			bi.uom AS stock_uom
		FROM `tabBOM Item` bi
		JOIN `tabBOM` b ON b.name = bi.parent
		WHERE bi.bom_no = %(bom_name)s
			AND b.docstatus = 1
		ORDER BY b.item, bi.parent
		""",
		{"bom_name": bom_name},
		as_dict=True,
	)

	bom_names = [d.value for d in data]
	expandable = _get_boms_with_parents(bom_names) if bom_names else set()
	for d in data:
		d.expandable = 1 if d.value in expandable else 0

	return data


def _get_boms_with_parents(bom_names):
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT bi.bom_no
		FROM `tabBOM Item` bi
		JOIN `tabBOM` b ON b.name = bi.parent
		WHERE bi.bom_no IN %(bom_names)s
			AND b.docstatus = 1
		""",
		{"bom_names": tuple(bom_names)},
		as_dict=True,
	)
	return {r.bom_no for r in rows}


@frappe.whitelist()
def get_bom_components(bom_name):
	bom_doc = frappe.get_cached_doc("BOM", bom_name)
	frappe.has_permission("BOM", doc=bom_doc, throw=True)

	rows = frappe.get_all(
		"BOM Item",
		fields=["item_code", "item_name", "bom_no", "qty", "stock_uom"],
		filters=[["parent", "=", bom_name]],
		order_by="idx",
	)
	return rows
