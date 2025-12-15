"""
Delivery Note Storage API

Functions to get default storage locations for Delivery Note items
"""

import json

import frappe
from frappe import _


def _get_excluded_storages():
	"""
	Get list of storages to exclude from default storage selection.
	Currently excludes default WIP storage from Manufacturing Settings.

	Returns:
		List of storage names to exclude
	"""
	excluded = []

	# Exclude default WIP storage
	default_wip_storage = frappe.db.get_single_value(
		"Manufacturing Settings", "default_wip_storage"
	)
	if default_wip_storage:
		excluded.append(default_wip_storage)

	return excluded


@frappe.whitelist()
def get_default_storage_for_item(item_code, warehouse):
	"""
	Get default storage for an item in a warehouse.
	Returns storage with available stock, or last storage where stock existed.
	Excludes default WIP storage from Manufacturing Settings.

	Args:
		item_code: Item code
		warehouse: Warehouse

	Returns:
		Storage name or None
	"""
	if not item_code or not warehouse:
		return None

	# Get storages to exclude
	excluded_storages = _get_excluded_storages()

	# Build exclusion clause
	exclusion_clause = ""
	params = {'item_code': item_code, 'warehouse': warehouse}

	if excluded_storages:
		exclusion_placeholders = ", ".join([f"%(excluded_{i})s" for i in range(len(excluded_storages))])
		exclusion_clause = f"AND sle.storage NOT IN ({exclusion_placeholders})"
		for i, storage in enumerate(excluded_storages):
			params[f'excluded_{i}'] = storage

	# First try: Get storage with available stock (FIFO order)
	storage_with_stock = frappe.db.sql(f"""
		SELECT
			sle.storage,
			SUM(sle.actual_qty) as available_qty
		FROM `tabStock Ledger Entry` sle
		WHERE
			sle.item_code = %(item_code)s
			AND sle.warehouse = %(warehouse)s
			AND sle.storage IS NOT NULL
			AND sle.storage != ''
			AND sle.is_cancelled = 0
			AND sle.docstatus < 2
			{exclusion_clause}
		GROUP BY sle.storage
		HAVING available_qty > 0
		ORDER BY MIN(sle.posting_date), MIN(sle.posting_time), MIN(sle.creation)
		LIMIT 1
	""", params, as_dict=True)

	if storage_with_stock:
		return storage_with_stock[0].storage

	# Second try: Get last storage where stock existed (most recent transaction)
	last_storage = frappe.db.sql(f"""
		SELECT sle.storage
		FROM `tabStock Ledger Entry` sle
		WHERE
			sle.item_code = %(item_code)s
			AND sle.warehouse = %(warehouse)s
			AND sle.storage IS NOT NULL
			AND sle.storage != ''
			AND sle.is_cancelled = 0
			AND sle.docstatus < 2
			{exclusion_clause}
		ORDER BY sle.posting_date DESC, sle.posting_time DESC, sle.creation DESC
		LIMIT 1
	""", params, as_dict=True)

	if last_storage:
		return last_storage[0].storage

	return None


@frappe.whitelist()
def get_default_storage_for_items(items_json):
	"""
	Get default storage for multiple items.

	Args:
		items_json: JSON string of items with item_code, warehouse, row_name

	Returns:
		List of dicts with row_name and default_storage
	"""
	items = json.loads(items_json) if isinstance(items_json, str) else items_json

	if not items:
		return []

	result = []

	for item in items:
		item_code = item.get('item_code')
		warehouse = item.get('warehouse')
		row_name = item.get('row_name')

		default_storage = get_default_storage_for_item(item_code, warehouse)

		result.append({
			'row_name': row_name,
			'default_storage': default_storage
		})

	return result
