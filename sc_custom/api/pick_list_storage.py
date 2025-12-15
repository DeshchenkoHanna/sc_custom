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


@frappe.whitelist()
def get_pick_list_items_storage(pick_list):
	"""
	Get Pick List items with storage field for Stock Entry creation.
	This method ignores permissions to allow Stock Entry creation.

	Args:
		pick_list: Pick List name

	Returns:
		List of Pick List Items with storage field
	"""
	if not pick_list:
		return []

	return frappe.get_all(
		"Pick List Item",
		filters={"parent": pick_list},
		fields=["item_code", "warehouse", "picked_qty", "storage"],
		order_by="idx",
		ignore_permissions=True
	)


@frappe.whitelist()
def get_storage_for_work_order_items(work_order, items_json):
	"""
	Get available storage locations for Work Order items.
	Used when creating Stock Entry "Material Transfer for Manufacture" directly from Work Order.

	Args:
		work_order: Work Order name
		items_json: JSON string of Stock Entry items with item_code, s_warehouse, qty

	Returns:
		List of dicts with storage allocated for each item (by idx)
	"""
	import json

	if not work_order:
		return []

	items = json.loads(items_json) if isinstance(items_json, str) else items_json
	if not items:
		return []

	result = []

	# Group items by item_code + warehouse to handle same item from same warehouse
	warehouse_groups = {}
	for idx, item in enumerate(items):
		item_code = item.get('item_code')
		warehouse = item.get('s_warehouse')
		qty = float(item.get('qty') or item.get('transfer_qty') or 0)

		if not item_code or not warehouse or qty <= 0:
			result.append({
				'idx': idx,
				'storage': None
			})
			continue

		key = f"{item_code}|||{warehouse}"
		if key not in warehouse_groups:
			warehouse_groups[key] = {
				'item_code': item_code,
				'warehouse': warehouse,
				'rows': []
			}
		warehouse_groups[key]['rows'].append({
			'idx': idx,
			'qty': qty
		})

	# Process each group and allocate storage using FIFO
	for key, group in warehouse_groups.items():
		item_code = group['item_code']
		warehouse = group['warehouse']
		total_qty = sum(r['qty'] for r in group['rows'])

		# Get available storage locations ordered by FIFO
		storage_locations = get_available_storage_locations(item_code, warehouse, total_qty)

		if not storage_locations:
			# No storage data - return rows without storage
			for row_info in group['rows']:
				result.append({
					'idx': row_info['idx'],
					'storage': None
				})
			continue

		# Allocate storage to rows in FIFO order
		storage_idx = 0
		remaining_in_storage = storage_locations[storage_idx]['qty_to_pick'] if storage_locations else 0

		for row_info in group['rows']:
			qty_needed = row_info['qty']
			allocated_storage = None

			# Find storage with enough quantity
			while qty_needed > 0 and storage_idx < len(storage_locations):
				current_storage = storage_locations[storage_idx]['storage']

				if remaining_in_storage >= qty_needed:
					# Current storage can fulfill this row
					allocated_storage = current_storage
					remaining_in_storage -= qty_needed
					qty_needed = 0
				else:
					# Take what we can and move to next storage
					if remaining_in_storage > 0:
						allocated_storage = current_storage  # Use first storage found
						qty_needed -= remaining_in_storage

					storage_idx += 1
					if storage_idx < len(storage_locations):
						remaining_in_storage = storage_locations[storage_idx]['qty_to_pick']
					else:
						remaining_in_storage = 0

			result.append({
				'idx': row_info['idx'],
				'storage': allocated_storage
			})

	# Sort by original index
	result.sort(key=lambda x: x['idx'])

	return result


def _get_batch_based_on_setting():
	"""Get the batch/serial selection method from Stock Settings."""
	return frappe.db.get_single_value("Stock Settings", "pick_serial_and_batch_based_on") or "FIFO"


def _get_available_batches_for_item(item_code, warehouse=None, required_qty=0, based_on="FIFO",
									priority_warehouses=None, exclude_warehouses=None):
	"""
	Get available batches for an item using the same algorithm as Pick List.
	Uses Serial and Batch Bundle for batch tracking (ERPNext v15+).

	Args:
		item_code: Item code
		warehouse: Optional warehouse filter
		required_qty: Required quantity
		based_on: FIFO, LIFO, or Expiry
		priority_warehouses: List of warehouses to prioritize (check first)
		exclude_warehouses: List of warehouses to exclude

	Returns:
		List of dicts with batch_no, warehouse, storage, qty
	"""
	from frappe.query_builder.functions import Sum
	from frappe.utils import today

	stock_ledger_entry = frappe.qb.DocType("Stock Ledger Entry")
	batch_ledger = frappe.qb.DocType("Serial and Batch Entry")
	batch_table = frappe.qb.DocType("Batch")

	query = (
		frappe.qb.from_(stock_ledger_entry)
		.inner_join(batch_ledger)
		.on(stock_ledger_entry.serial_and_batch_bundle == batch_ledger.parent)
		.inner_join(batch_table)
		.on(batch_ledger.batch_no == batch_table.name)
		.select(
			batch_ledger.batch_no,
			batch_ledger.warehouse,
			stock_ledger_entry.storage,
			Sum(batch_ledger.qty).as_("qty"),
		)
		.where(batch_table.disabled == 0)
		.where(stock_ledger_entry.is_cancelled == 0)
		.where((batch_table.expiry_date >= today()) | (batch_table.expiry_date.isnull()))
		.where(stock_ledger_entry.item_code == item_code)
		.groupby(batch_ledger.batch_no, batch_ledger.warehouse, stock_ledger_entry.storage)
		.having(Sum(batch_ledger.qty) > 0)
	)

	if warehouse:
		if isinstance(warehouse, list):
			query = query.where(stock_ledger_entry.warehouse.isin(warehouse))
		else:
			query = query.where(stock_ledger_entry.warehouse == warehouse)

	# Exclude warehouses (e.g., WIP warehouse for Material Transfer for Manufacture)
	if exclude_warehouses:
		if isinstance(exclude_warehouses, list):
			query = query.where(stock_ledger_entry.warehouse.notin(exclude_warehouses))
		else:
			query = query.where(stock_ledger_entry.warehouse != exclude_warehouses)

	# Apply ordering based on setting
	if based_on == "LIFO":
		query = query.orderby(batch_table.creation, order=frappe.qb.desc)
	elif based_on == "Expiry":
		query = query.orderby(batch_table.expiry_date)
	else:  # FIFO
		query = query.orderby(batch_table.creation)

	data = query.run(as_dict=True)

	# Reorder to prioritize specific warehouses
	if priority_warehouses and data:
		data = _prioritize_warehouses(data, priority_warehouses)

	return data


def _prioritize_warehouses(data, priority_warehouses):
	"""
	Reorder data to prioritize specific warehouses.
	Items from priority warehouses come first, then others.
	"""
	if not priority_warehouses:
		return data

	priority_set = set(priority_warehouses) if isinstance(priority_warehouses, list) else {priority_warehouses}

	priority_items = [row for row in data if row.get('warehouse') in priority_set]
	other_items = [row for row in data if row.get('warehouse') not in priority_set]

	return priority_items + other_items


def _get_available_serial_nos_for_item(item_code, warehouse=None, required_qty=0, based_on="FIFO",
									   priority_warehouses=None, exclude_warehouses=None):
	"""
	Get available serial numbers for an item using the same algorithm as Pick List.

	Args:
		item_code: Item code
		warehouse: Optional warehouse filter
		required_qty: Required quantity
		based_on: FIFO, LIFO, or Expiry
		priority_warehouses: List of warehouses to prioritize (check first)
		exclude_warehouses: List of warehouses to exclude

	Returns:
		List of dicts with serial_no, warehouse, storage, batch_no (if applicable)
	"""
	from frappe.query_builder.functions import Coalesce

	sn = frappe.qb.DocType("Serial No")

	# Determine order direction
	order_dir = frappe.qb.desc if based_on == "LIFO" else frappe.qb.asc

	query = (
		frappe.qb.from_(sn)
		.select(
			sn.name.as_("serial_no"),
			sn.warehouse,
			sn.batch_no,
		)
		.where(sn.item_code == item_code)
		.where(Coalesce(sn.warehouse, "") != "")
	)

	if warehouse:
		if isinstance(warehouse, list):
			query = query.where(sn.warehouse.isin(warehouse))
		else:
			query = query.where(sn.warehouse == warehouse)

	# Exclude warehouses (e.g., WIP warehouse for Material Transfer for Manufacture)
	if exclude_warehouses:
		if isinstance(exclude_warehouses, list):
			query = query.where(sn.warehouse.notin(exclude_warehouses))
		else:
			query = query.where(sn.warehouse != exclude_warehouses)

	# Apply ordering based on setting
	if based_on == "Expiry":
		query = query.orderby(sn.amc_expiry_date, order=order_dir)
	else:
		query = query.orderby(sn.creation, order=order_dir)

	# Don't limit here if we need to prioritize - we'll limit after prioritization
	if required_qty > 0 and not priority_warehouses:
		query = query.limit(int(required_qty))

	data = query.run(as_dict=True)

	# Get storage for each serial no from Stock Ledger Entry
	for row in data:
		if row.warehouse:
			storage = frappe.db.get_value(
				"Stock Ledger Entry",
				{
					"item_code": item_code,
					"warehouse": row.warehouse,
					"serial_no": row.serial_no,
					"is_cancelled": 0
				},
				"storage",
				order_by="posting_date desc, posting_time desc, creation desc"
			)
			row['storage'] = storage

	# Reorder to prioritize specific warehouses and then limit
	if priority_warehouses and data:
		data = _prioritize_warehouses(data, priority_warehouses)
		if required_qty > 0:
			data = data[:int(required_qty)]

	return data


def _get_available_stock_for_other_item(item_code, warehouse=None, based_on="FIFO",
										priority_warehouses=None, exclude_warehouses=None):
	"""
	Get available stock for items without serial/batch tracking.
	Uses Bin for quantity and SLE for storage allocation.

	Args:
		item_code: Item code
		warehouse: Optional warehouse filter
		based_on: FIFO, LIFO (affects storage ordering)
		priority_warehouses: List of warehouses to prioritize (check first)
		exclude_warehouses: List of warehouses to exclude

	Returns:
		List of dicts with warehouse, storage, qty
	"""
	# First get warehouses with stock from Bin
	bin_table = frappe.qb.DocType("Bin")
	query = (
		frappe.qb.from_(bin_table)
		.select(bin_table.warehouse, bin_table.actual_qty.as_("qty"))
		.where((bin_table.item_code == item_code) & (bin_table.actual_qty > 0))
		.orderby(bin_table.creation)
	)

	if warehouse:
		if isinstance(warehouse, list):
			query = query.where(bin_table.warehouse.isin(warehouse))
		else:
			query = query.where(bin_table.warehouse == warehouse)

	# Exclude warehouses (e.g., WIP warehouse for Material Transfer for Manufacture)
	if exclude_warehouses:
		if isinstance(exclude_warehouses, list):
			query = query.where(bin_table.warehouse.notin(exclude_warehouses))
		else:
			query = query.where(bin_table.warehouse != exclude_warehouses)

	bin_data = query.run(as_dict=True)

	if not bin_data:
		return []

	# Reorder to prioritize specific warehouses
	if priority_warehouses and bin_data:
		bin_data = _prioritize_warehouses(bin_data, priority_warehouses)

	# For each warehouse, get storage breakdown from SLE
	result = []
	order_direction = "DESC" if based_on == "LIFO" else "ASC"

	for bin_row in bin_data:
		wh = bin_row.warehouse
		# Get storage breakdown for this warehouse
		storage_data = frappe.db.sql(f"""
			SELECT
				sle.storage,
				SUM(sle.actual_qty) as qty
			FROM `tabStock Ledger Entry` sle
			WHERE
				sle.item_code = %(item_code)s
				AND sle.warehouse = %(warehouse)s
				AND sle.is_cancelled = 0
				AND sle.docstatus < 2
			GROUP BY sle.storage
			HAVING qty > 0
			ORDER BY MIN(sle.posting_date) {order_direction},
					 MIN(sle.posting_time) {order_direction},
					 MIN(sle.creation) {order_direction}
		""", {
			'item_code': item_code,
			'warehouse': wh
		}, as_dict=True)

		if storage_data:
			for storage_row in storage_data:
				result.append({
					'warehouse': wh,
					'storage': storage_row.storage,
					'qty': float(storage_row.qty or 0)
				})
		else:
			# No storage data, add warehouse without storage
			result.append({
				'warehouse': wh,
				'storage': None,
				'qty': float(bin_row.qty or 0)
			})

	return result


@frappe.whitelist()
def get_available_stock_for_items(items_json, company=None, work_order=None, purpose=None):
	"""
	Get available warehouse, storage, and batch/serial locations for items based on actual stock.
	Uses the same FIFO/LIFO/Expiry algorithm as Pick List, respecting Stock Settings.

	Used when creating Stock Entry "Material Transfer for Manufacture" from Work Order
	to override the default source warehouse from Work Order with actual available stock.

	Args:
		items_json: JSON string of items with item_code and qty
		company: Company for Serial and Batch Bundle creation
		work_order: Work Order name (to get priority warehouses from Work Order items)
		purpose: Stock Entry purpose (to determine if WIP warehouse should be excluded)

	Returns:
		List of dicts with warehouse, storage, batch_no, serial_nos allocated for each item (by idx)
	"""
	import json

	items = json.loads(items_json) if isinstance(items_json, str) else items_json
	if not items:
		return []

	# Get the selection method from Stock Settings
	based_on = _get_batch_based_on_setting()

	# Get priority warehouses from Work Order and exclude WIP warehouse for Material Transfer
	priority_warehouses = {}  # item_code -> list of warehouses
	exclude_warehouses = None

	if work_order and purpose == "Material Transfer for Manufacture":
		# Get source warehouses from Work Order items (these should be checked first)
		wo_items = frappe.get_all(
			"Work Order Item",
			filters={"parent": work_order},
			fields=["item_code", "source_warehouse"]
		)
		for wo_item in wo_items:
			if wo_item.source_warehouse:
				if wo_item.item_code not in priority_warehouses:
					priority_warehouses[wo_item.item_code] = []
				if wo_item.source_warehouse not in priority_warehouses[wo_item.item_code]:
					priority_warehouses[wo_item.item_code].append(wo_item.source_warehouse)

		# Get default WIP warehouse to exclude (we're transferring TO WIP, not FROM it)
		wip_warehouse = frappe.db.get_value("Work Order", work_order, "wip_warehouse")
		if not wip_warehouse:
			wip_warehouse = frappe.db.get_single_value("Manufacturing Settings", "default_wip_warehouse")
		exclude_warehouses = wip_warehouse

	result = []

	for idx, item in enumerate(items):
		item_code = item.get('item_code')
		required_qty = float(item.get('qty') or item.get('transfer_qty') or 0)

		if not item_code or required_qty <= 0:
			result.append({
				'idx': idx,
				'warehouse': None,
				'storage': None,
				'batch_no': None,
				'serial_nos': [],
				'available_qty': 0
			})
			continue

		# Check item properties
		item_details = frappe.get_cached_value(
			"Item", item_code,
			["has_serial_no", "has_batch_no"],
			as_dict=True
		)
		has_serial_no = item_details.get("has_serial_no") if item_details else 0
		has_batch_no = item_details.get("has_batch_no") if item_details else 0

		# Get priority warehouses for this specific item
		item_priority_warehouses = priority_warehouses.get(item_code)

		allocations = []
		remaining_qty = required_qty

		if has_batch_no:
			# Get batches using Pick List algorithm
			batch_data = _get_available_batches_for_item(
				item_code,
				warehouse=None,
				required_qty=required_qty,
				based_on=based_on,
				priority_warehouses=item_priority_warehouses,
				exclude_warehouses=exclude_warehouses
			)

			for batch_row in batch_data:
				if remaining_qty <= 0:
					break

				batch_qty = float(batch_row.get('qty') or 0)
				if batch_qty <= 0:
					continue

				qty_to_take = min(batch_qty, remaining_qty)

				allocation = {
					'warehouse': batch_row.get('warehouse'),
					'storage': batch_row.get('storage'),
					'batch_no': batch_row.get('batch_no'),
					'qty': qty_to_take,
					'available_qty': batch_qty
				}

				# If also has serial no, get serial nos for this batch
				if has_serial_no:
					serial_data = _get_available_serial_nos_for_item(
						item_code,
						warehouse=batch_row.get('warehouse'),
						required_qty=qty_to_take,
						based_on=based_on,
						priority_warehouses=item_priority_warehouses,
						exclude_warehouses=exclude_warehouses
					)
					# Filter to only serials in this batch
					batch_serials = [
						s['serial_no'] for s in serial_data
						if s.get('batch_no') == batch_row.get('batch_no')
					][:int(qty_to_take)]
					allocation['serial_nos'] = batch_serials

				allocations.append(allocation)
				remaining_qty -= qty_to_take

		elif has_serial_no:
			# Get serial nos using Pick List algorithm
			serial_data = _get_available_serial_nos_for_item(
				item_code,
				warehouse=None,
				required_qty=required_qty,
				based_on=based_on,
				priority_warehouses=item_priority_warehouses,
				exclude_warehouses=exclude_warehouses
			)

			# Group by warehouse + storage
			warehouse_groups = {}
			for serial_row in serial_data:
				key = (serial_row.get('warehouse'), serial_row.get('storage'))
				if key not in warehouse_groups:
					warehouse_groups[key] = {
						'warehouse': serial_row.get('warehouse'),
						'storage': serial_row.get('storage'),
						'serial_nos': []
					}
				warehouse_groups[key]['serial_nos'].append(serial_row['serial_no'])

			for key, group in warehouse_groups.items():
				if remaining_qty <= 0:
					break

				serial_nos = group['serial_nos']
				qty_to_take = min(len(serial_nos), remaining_qty)

				allocations.append({
					'warehouse': group['warehouse'],
					'storage': group['storage'],
					'batch_no': None,
					'serial_nos': serial_nos[:int(qty_to_take)],
					'qty': qty_to_take,
					'available_qty': len(serial_nos)
				})
				remaining_qty -= qty_to_take

		else:
			# No batch/serial - get stock from warehouse/storage
			stock_data = _get_available_stock_for_other_item(
				item_code,
				warehouse=None,
				based_on=based_on,
				priority_warehouses=item_priority_warehouses,
				exclude_warehouses=exclude_warehouses
			)

			for stock_row in stock_data:
				if remaining_qty <= 0:
					break

				stock_qty = float(stock_row.get('qty') or 0)
				if stock_qty <= 0:
					continue

				qty_to_take = min(stock_qty, remaining_qty)

				allocations.append({
					'warehouse': stock_row.get('warehouse'),
					'storage': stock_row.get('storage'),
					'batch_no': None,
					'serial_nos': [],
					'qty': qty_to_take,
					'available_qty': stock_qty
				})
				remaining_qty -= qty_to_take

		if allocations:
			# Return first allocation as primary, with additional allocations if split needed
			result.append({
				'idx': idx,
				'warehouse': allocations[0]['warehouse'],
				'storage': allocations[0]['storage'],
				'batch_no': allocations[0].get('batch_no'),
				'serial_nos': allocations[0].get('serial_nos', []),
				'available_qty': allocations[0]['available_qty'],
				'qty_allocated': allocations[0]['qty'],
				'has_serial_no': has_serial_no,
				'has_batch_no': has_batch_no,
				'has_split': len(allocations) > 1,
				'additional_allocations': allocations[1:] if len(allocations) > 1 else []
			})
		else:
			result.append({
				'idx': idx,
				'warehouse': None,
				'storage': None,
				'batch_no': None,
				'serial_nos': [],
				'available_qty': 0
			})

	return result


