import frappe


def sync_default_supplier_with_supplier_items(doc, method=None):
	"""Keep Item Defaults default_supplier equal to the first Item Supplier row.

	Runs on every save so it also covers items created or updated via REST API
	and Data Import. Syncs silently — the user-facing alert and the empty-
	supplier confirmation dialog live in public/js/item.js (form only).
	"""
	supplier = doc.supplier_items[0].supplier if doc.get("supplier_items") else None
	if not supplier:
		return

	if doc.get("item_defaults"):
		for d in doc.item_defaults:
			if d.default_supplier != supplier:
				d.default_supplier = supplier
	else:
		company = frappe.defaults.get_user_default("company") or frappe.defaults.get_global_default(
			"company"
		)
		if company:
			doc.append("item_defaults", {"company": company, "default_supplier": supplier})
