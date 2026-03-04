"""
Pick List Storage Management API

Functions to get available storage locations for Stock Entry items.
"""

import frappe
from frappe import _


@frappe.whitelist()
def get_pick_list_items_storage(pick_list):
	"""Get Pick List items with storage field for Stock Entry creation."""
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
def get_available_stock_for_items(items_json, company=None, work_order=None, purpose=None):
	"""Get available warehouse, storage, and batch/serial locations for items based on actual stock.

	Used when creating Stock Entry "Material Transfer for Manufacture" from Work Order.
	"""
	import json

	items = json.loads(items_json) if isinstance(items_json, str) else items_json
	if not items:
		return []

	based_on = _get_batch_based_on_setting()

	priority_warehouses = {}
	exclude_warehouses = None

	if work_order and purpose == "Material Transfer for Manufacture":
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

		item_details = frappe.get_cached_value(
			"Item", item_code,
			["has_serial_no", "has_batch_no"],
			as_dict=True
		)
		has_serial_no = item_details.get("has_serial_no") if item_details else 0
		has_batch_no = item_details.get("has_batch_no") if item_details else 0

		item_priority_warehouses = priority_warehouses.get(item_code)

		allocations = []
		remaining_qty = required_qty

		if has_batch_no:
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

				if has_serial_no:
					serial_data = _get_available_serial_nos_for_item(
						item_code,
						warehouse=batch_row.get('warehouse'),
						required_qty=qty_to_take,
						based_on=based_on,
						priority_warehouses=item_priority_warehouses,
						exclude_warehouses=exclude_warehouses
					)
					batch_serials = [
						s['serial_no'] for s in serial_data
						if s.get('batch_no') == batch_row.get('batch_no')
					][:int(qty_to_take)]
					allocation['serial_nos'] = batch_serials

				allocations.append(allocation)
				remaining_qty -= qty_to_take

		elif has_serial_no:
			serial_data = _get_available_serial_nos_for_item(
				item_code,
				warehouse=None,
				required_qty=required_qty,
				based_on=based_on,
				priority_warehouses=item_priority_warehouses,
				exclude_warehouses=exclude_warehouses
			)

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


def _get_batch_based_on_setting():
	"""Get the batch/serial selection method from Stock Settings."""
	return frappe.db.get_single_value("Stock Settings", "pick_serial_and_batch_based_on") or "FIFO"


def _get_available_batches_for_item(item_code, warehouse=None, required_qty=0, based_on="FIFO",
									priority_warehouses=None, exclude_warehouses=None):
	"""Get available batches for an item using Serial and Batch Bundle."""
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

	if exclude_warehouses:
		if isinstance(exclude_warehouses, list):
			query = query.where(stock_ledger_entry.warehouse.notin(exclude_warehouses))
		else:
			query = query.where(stock_ledger_entry.warehouse != exclude_warehouses)

	if based_on == "LIFO":
		query = query.orderby(batch_table.creation, order=frappe.qb.desc)
	elif based_on == "Expiry":
		query = query.orderby(batch_table.expiry_date)
	else:
		query = query.orderby(batch_table.creation)

	data = query.run(as_dict=True)

	if priority_warehouses and data:
		data = _prioritize_warehouses(data, priority_warehouses)

	return data


def _prioritize_warehouses(data, priority_warehouses):
	"""Reorder data to prioritize specific warehouses."""
	if not priority_warehouses:
		return data

	priority_set = set(priority_warehouses) if isinstance(priority_warehouses, list) else {priority_warehouses}

	priority_items = [row for row in data if row.get('warehouse') in priority_set]
	other_items = [row for row in data if row.get('warehouse') not in priority_set]

	return priority_items + other_items


def _get_available_serial_nos_for_item(item_code, warehouse=None, required_qty=0, based_on="FIFO",
									   priority_warehouses=None, exclude_warehouses=None):
	"""Get available serial numbers for an item."""
	from frappe.query_builder.functions import Coalesce

	sn = frappe.qb.DocType("Serial No")

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

	if exclude_warehouses:
		if isinstance(exclude_warehouses, list):
			query = query.where(sn.warehouse.notin(exclude_warehouses))
		else:
			query = query.where(sn.warehouse != exclude_warehouses)

	if based_on == "Expiry":
		query = query.orderby(sn.amc_expiry_date)
	elif based_on == "LIFO":
		query = query.orderby(sn.creation, order=frappe.qb.desc)
	else:
		query = query.orderby(sn.creation)

	if required_qty > 0 and not priority_warehouses:
		query = query.limit(int(required_qty))

	data = query.run(as_dict=True)

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

	if priority_warehouses and data:
		data = _prioritize_warehouses(data, priority_warehouses)
		if required_qty > 0:
			data = data[:int(required_qty)]

	return data


def _get_available_stock_for_other_item(item_code, warehouse=None, based_on="FIFO",
										priority_warehouses=None, exclude_warehouses=None):
	"""Get available stock for items without serial/batch tracking."""
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

	if exclude_warehouses:
		if isinstance(exclude_warehouses, list):
			query = query.where(bin_table.warehouse.notin(exclude_warehouses))
		else:
			query = query.where(bin_table.warehouse != exclude_warehouses)

	bin_data = query.run(as_dict=True)

	if not bin_data:
		return []

	if priority_warehouses and bin_data:
		bin_data = _prioritize_warehouses(bin_data, priority_warehouses)

	result = []
	order_direction = "DESC" if based_on == "LIFO" else "ASC"

	for bin_row in bin_data:
		wh = bin_row.warehouse
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
			result.append({
				'warehouse': wh,
				'storage': None,
				'qty': float(bin_row.qty or 0)
			})

	return result
