import frappe
from frappe.utils import cint, sbool


@frappe.whitelist()
def get_used_in_boms(item_code, parent=None, is_root=False, include_sub_assemblies=0, do_not_explode=1):
	is_root = sbool(is_root)
	include_sub_assemblies = cint(include_sub_assemblies)
	do_not_explode = cint(do_not_explode)

	if is_root:
		return _get_root_boms(item_code, include_sub_assemblies)
	elif parent:
		return _get_bom_children(parent, do_not_explode)

	return []


def _get_root_boms(item_code, include_sub_assemblies):
	if include_sub_assemblies:
		data = frappe.db.sql(
			"""
			SELECT
				bei.parent AS value,
				b.item AS item_code,
				b.item_name,
				bei.stock_qty AS qty,
				bei.stock_uom
			FROM `tabBOM Explosion Item` bei
			JOIN `tabBOM` b ON b.name = bei.parent
			WHERE bei.item_code = %(item_code)s
			ORDER BY b.is_default DESC, b.is_active DESC
			""",
			{"item_code": item_code},
			as_dict=True,
		)
	else:
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
			ORDER BY b.is_default DESC, b.is_active DESC
			""",
			{"item_code": item_code},
			as_dict=True,
		)

	bom_names = [d.value for d in data]
	boms_with_children = _get_boms_with_children(bom_names) if bom_names else set()

	for d in data:
		d.expandable = 1 if d.value in boms_with_children else 0

	return data


def _get_bom_children(parent, do_not_explode=1):
	bom_doc = frappe.get_cached_doc("BOM", parent)
	frappe.has_permission("BOM", doc=bom_doc, throw=True)

	bom_items = frappe.get_all(
		"BOM Item",
		fields=["item_code", "bom_no as value", "stock_qty as qty", "stock_uom"],
		filters=[["parent", "=", parent]],
		order_by="idx",
	)

	item_names = tuple(d.get("item_code") for d in bom_items)
	if item_names:
		items = frappe.get_list(
			"Item",
			fields=["name", "item_name", "include_item_in_manufacturing"],
			filters=[["name", "in", item_names]],
		)
		item_map = {i.name: i for i in items}
	else:
		item_map = {}

	default_bom_map = {}
	if not do_not_explode:
		# Find default BOMs for manufacturing items without explicit bom_no
		mfg_items_without_bom = [
			d.item_code for d in bom_items
			if not d.value and item_map.get(d.item_code, {}).get("include_item_in_manufacturing")
		]
		if mfg_items_without_bom:
			default_boms = frappe.get_all(
				"BOM",
				filters={"item": ["in", mfg_items_without_bom], "is_default": 1, "docstatus": 1},
				fields=["name", "item"],
			)
			default_bom_map = {b.item: b.name for b in default_boms}

	for d in bom_items:
		item_info = item_map.get(d.item_code, {})
		d.item_name = item_info.get("item_name", d.item_code)
		if not do_not_explode and not d.value and d.item_code in default_bom_map:
			d.value = default_bom_map[d.item_code]
		d.expandable = 1 if d.value else 0

	return bom_items


def _get_boms_with_children(bom_names):
	children = frappe.get_all(
		"BOM Item",
		filters=[["parent", "in", bom_names]],
		fields=["parent"],
		group_by="parent",
	)
	return {d.parent for d in children}
