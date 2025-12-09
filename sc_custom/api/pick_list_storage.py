"""
Pick List Storage Management API

Functions to get available storage locations for items in pick lists
"""

import frappe
from frappe import _


@frappe.whitelist()
def get_available_storage_locations(item_code, warehouse, required_qty):
	"""
	Get available storage locations for an item in a specific warehouse.

	Args:
		item_code: Item code to search for
		warehouse: Warehouse where storage is located
		required_qty: Required quantity to pick

	Returns:
		List of dicts with storage and available qty, ordered by FIFO
	"""
	if not item_code or not warehouse:
		return []

	required_qty = float(required_qty or 0)
	if required_qty <= 0:
		return []

	# Query Stock Ledger Entry to get storage quantities
	# Group by storage and sum qty_after_transaction
	# Order by creation (FIFO - First In, First Out)
	storage_data = frappe.db.sql("""
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
		GROUP BY sle.storage
		HAVING available_qty > 0
		ORDER BY MIN(sle.posting_date), MIN(sle.posting_time), MIN(sle.creation)
	""", {
		'item_code': item_code,
		'warehouse': warehouse
	}, as_dict=True)

	if not storage_data:
		return []

	# Build result list with storage locations
	result = []
	remaining_qty = required_qty

	for row in storage_data:
		if remaining_qty <= 0:
			break

		storage = row.get('storage')
		available_qty = float(row.get('available_qty') or 0)

		if available_qty <= 0:
			continue

		# Take the minimum of available qty and remaining qty
		qty_to_pick = min(available_qty, remaining_qty)

		result.append({
			'storage': storage,
			'available_qty': available_qty,
			'qty_to_pick': qty_to_pick
		})

		remaining_qty -= qty_to_pick

	return result


@frappe.whitelist()
def allocate_storage_for_pick_list(locations_json):
	"""
	Allocate storage locations for all items in a Pick List table.
	Takes into account the entire table to avoid double-allocation.

	Args:
		locations_json: JSON string of locations table data

	Returns:
		List of dicts with storage allocated for each row
	"""
	import json
	locations = json.loads(locations_json) if isinstance(locations_json, str) else locations_json

	if not locations:
		return []

	# Group by item_code + warehouse
	warehouse_groups = {}
	for idx, row in enumerate(locations):
		if not row.get('item_code') or not row.get('warehouse') or not row.get('stock_qty'):
			continue

		key = f"{row['item_code']}|||{row['warehouse']}"
		if key not in warehouse_groups:
			warehouse_groups[key] = {
				'item_code': row['item_code'],
				'warehouse': row['warehouse'],
				'rows': []
			}
		warehouse_groups[key]['rows'].append({
			'idx': idx,
			'name': row.get('name'),
			'qty': float(row.get('stock_qty', 0)),
			'existing_storage': row.get('storage')
		})

	# Process each group and allocate storage
	result = []

	for key, group in warehouse_groups.items():
		item_code = group['item_code']
		warehouse = group['warehouse']

		# Get all available storage locations (ordered by FIFO)
		storage_locations = get_available_storage_locations(
			item_code,
			warehouse,
			sum(r['qty'] for r in group['rows'])
		)

		if not storage_locations:
			# No storage data - return rows without storage
			for row_info in group['rows']:
				result.append({
					'idx': row_info['idx'],
					'name': row_info['name'],
					'storage': None
				})
			continue

		# Allocate storage to rows in order
		storage_idx = 0
		remaining_in_storage = storage_locations[storage_idx]['qty_to_pick']

		for row_info in group['rows']:
			qty_needed = row_info['qty']
			allocations = []  # Can have multiple if split needed

			# Find storage with enough quantity
			while qty_needed > 0 and storage_idx < len(storage_locations):
				current_storage = storage_locations[storage_idx]['storage']

				if remaining_in_storage >= qty_needed:
					# Current storage can fulfill entire (or remaining) row
					allocations.append({
						'storage': current_storage,
						'qty': qty_needed
					})
					remaining_in_storage -= qty_needed
					qty_needed = 0
				else:
					# Take what we can from current storage
					if remaining_in_storage > 0:
						allocations.append({
							'storage': current_storage,
							'qty': remaining_in_storage
						})
						qty_needed -= remaining_in_storage

					# Move to next storage
					storage_idx += 1
					if storage_idx < len(storage_locations):
						remaining_in_storage = storage_locations[storage_idx]['qty_to_pick']
					else:
						remaining_in_storage = 0

			# Add result for original row
			result.append({
				'idx': row_info['idx'],
				'name': row_info['name'],
				'storage': allocations[0]['storage'] if allocations else None,
				'qty': allocations[0]['qty'] if allocations else 0,
				'split': len(allocations) > 1,  # Flag if need to split
				'additional_allocations': allocations[1:] if len(allocations) > 1 else []
			})

	# Sort by original index
	result.sort(key=lambda x: x['idx'])

	return result
