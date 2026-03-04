"""
Custom query functions for SC Custom.

Extends standard ERPNext queries with storage dimension support.
"""

import frappe
from frappe.utils import cint, today


@frappe.whitelist()
def get_batch_no(doctype, txt, searchfield, start, page_len, filters):
	"""Custom batch query that supports storage filtering.

	Extends the standard get_batch_no to filter by storage when provided.
	Falls back to the standard query when no storage filter is set.
	"""
	storage = filters.pop("storage", None) if isinstance(filters, dict) else None

	if not storage:
		from erpnext.controllers.queries import get_batch_no as standard_get_batch_no
		return standard_get_batch_no(doctype, txt, searchfield, start, page_len, filters)

	item_code = filters.get("item_code")
	warehouse = filters.get("warehouse")

	if not item_code:
		return []

	# Get batches from SABE/SABB with storage filter
	sabe_batches = _get_batches_from_sabb_with_storage(item_code, warehouse, storage, txt)

	# Get batches from SLE with storage filter (legacy path)
	sle_batches = _get_batches_from_sle_with_storage(item_code, warehouse, storage, txt)

	# Merge and deduplicate
	batches = {}
	for batch in sle_batches + sabe_batches:
		if batch[0] not in batches:
			batches[batch[0]] = list(batch)
		else:
			batches[batch[0]][1] += batch[1]

	return [tuple(b) for b in batches.values() if b[1] > 0]


def _get_batches_from_sabb_with_storage(item_code, warehouse, storage, txt=None):
	"""Get batches from Serial and Batch Bundle filtered by storage."""
	conditions = ""
	values = {
		"item_code": item_code,
		"storage": storage,
		"today": today(),
	}

	if warehouse:
		conditions += " AND sabe.warehouse = %(warehouse)s"
		values["warehouse"] = warehouse

	if txt:
		conditions += " AND sabe.batch_no LIKE %(txt)s"
		values["txt"] = f"%{txt}%"

	return frappe.db.sql("""
		SELECT
			sabe.batch_no,
			SUM(sabe.qty) as qty
		FROM `tabSerial and Batch Entry` sabe
		JOIN `tabSerial and Batch Bundle` sabb ON sabe.parent = sabb.name
		JOIN `tabBatch` b ON b.name = sabe.batch_no
		WHERE
			sabb.item_code = %(item_code)s
			AND sabb.storage = %(storage)s
			AND sabb.docstatus = 1
			AND sabb.is_cancelled = 0
			AND b.disabled = 0
			AND (b.expiry_date >= %(today)s OR b.expiry_date IS NULL)
			{conditions}
		GROUP BY sabe.batch_no
		HAVING SUM(sabe.qty) > 0
		ORDER BY MIN(sabb.creation)
	""".format(conditions=conditions), values, as_list=True) or []


def _get_batches_from_sle_with_storage(item_code, warehouse, storage, txt=None):
	"""Get batches from Stock Ledger Entry filtered by storage (legacy path)."""
	conditions = ""
	values = {
		"item_code": item_code,
		"storage": storage,
		"today": today(),
	}

	if warehouse:
		conditions += " AND sle.warehouse = %(warehouse)s"
		values["warehouse"] = warehouse

	if txt:
		conditions += " AND sle.batch_no LIKE %(txt)s"
		values["txt"] = f"%{txt}%"

	return frappe.db.sql("""
		SELECT
			sle.batch_no,
			SUM(sle.actual_qty) as qty
		FROM `tabStock Ledger Entry` sle
		JOIN `tabBatch` b ON b.name = sle.batch_no
		WHERE
			sle.item_code = %(item_code)s
			AND sle.storage = %(storage)s
			AND sle.is_cancelled = 0
			AND sle.batch_no IS NOT NULL
			AND sle.batch_no != ''
			AND b.disabled = 0
			AND (b.expiry_date >= %(today)s OR b.expiry_date IS NULL)
			{conditions}
		GROUP BY sle.batch_no
		HAVING SUM(sle.actual_qty) > 0
		ORDER BY MIN(sle.posting_date), MIN(sle.posting_time)
	""".format(conditions=conditions), values, as_list=True) or []


@frappe.whitelist()
def get_storage(doctype, txt, searchfield, start, page_len, filters):
	"""Get available storage locations with stock for an item in a warehouse.

	Returns storages from both SABB/SABE and SLE sources, with available qty.
	Used as set_query for the storage field in Pick List Item.
	"""
	item_code = filters.get("item_code") if isinstance(filters, dict) else None
	warehouse = filters.get("warehouse") if isinstance(filters, dict) else None

	if not item_code or not warehouse:
		return []

	txt_condition_sabb = ""
	txt_condition_sle = ""
	values = {
		"item_code": item_code,
		"warehouse": warehouse,
	}

	if txt:
		txt_condition_sabb = " AND sabb.storage LIKE %(txt)s"
		txt_condition_sle = " AND sle.storage LIKE %(txt)s"
		values["txt"] = f"%{txt}%"

	# Get storages from SABB/SABE (batch-tracked items)
	sabb_storages = frappe.db.sql("""
		SELECT
			sabb.storage,
			SUM(sabe.qty) as qty
		FROM `tabSerial and Batch Entry` sabe
		JOIN `tabSerial and Batch Bundle` sabb ON sabe.parent = sabb.name
		WHERE
			sabb.item_code = %(item_code)s
			AND sabe.warehouse = %(warehouse)s
			AND sabb.docstatus = 1
			AND sabb.is_cancelled = 0
			AND sabb.storage IS NOT NULL
			AND sabb.storage != ''
			{txt_condition}
		GROUP BY sabb.storage
		HAVING SUM(sabe.qty) > 0
		ORDER BY sabb.storage
	""".format(txt_condition=txt_condition_sabb), values, as_list=True) or []

	# Get storages from SLE (non-batch items or legacy)
	sle_storages = frappe.db.sql("""
		SELECT
			sle.storage,
			SUM(sle.actual_qty) as qty
		FROM `tabStock Ledger Entry` sle
		WHERE
			sle.item_code = %(item_code)s
			AND sle.warehouse = %(warehouse)s
			AND sle.is_cancelled = 0
			AND sle.storage IS NOT NULL
			AND sle.storage != ''
			{txt_condition}
		GROUP BY sle.storage
		HAVING SUM(sle.actual_qty) > 0
		ORDER BY sle.storage
	""".format(txt_condition=txt_condition_sle), values, as_list=True) or []

	# Merge and deduplicate — take max qty from either source
	storages = {}
	for row in sabb_storages + sle_storages:
		name = row[0]
		qty = row[1]
		if name not in storages:
			storages[name] = qty
		else:
			storages[name] = max(storages[name], qty)

	return [[s, f"Qty: {q}"] for s, q in sorted(storages.items())]


@frappe.whitelist()
def get_storage_for_autocomplete(item_code=None, warehouse=None, txt="", **kwargs):
	"""Get storage options formatted for Autocomplete control.

	Used in the Pick Serial/Batch dialog's storage field.
	Returns list of dicts with label, value, description for Autocomplete.
	"""
	if not item_code or not warehouse:
		return []

	filters = {"item_code": item_code, "warehouse": warehouse}
	results = get_storage("Pick List Item", txt or "", "storage", 0, 20, filters)
	return [{"label": row[0], "value": row[0], "description": str(row[1])} for row in results]


@frappe.whitelist()
def get_auto_batch_nos_with_storage(item_code, warehouse, storage, qty, based_on="FIFO"):
	"""Get auto batch numbers filtered by storage.

	Used by the Pick Serial/Batch dialog to show only batches in the selected storage.
	Returns list of dicts with batch_no and qty (same format as standard get_auto_data).
	"""
	qty = float(qty or 0)
	if not item_code or not warehouse or not storage or qty <= 0:
		return []

	conditions = ""
	values = {
		"item_code": item_code,
		"warehouse": warehouse,
		"storage": storage,
		"today": today(),
	}

	# Get batches from SABB/SABE with storage
	order_by = "MIN(sabb.creation)"
	if based_on == "LIFO":
		order_by = "MAX(sabb.creation) DESC"
	elif based_on == "Expiry":
		order_by = "b.expiry_date"

	batches = frappe.db.sql("""
		SELECT
			sabe.batch_no,
			SUM(sabe.qty) as qty
		FROM `tabSerial and Batch Entry` sabe
		JOIN `tabSerial and Batch Bundle` sabb ON sabe.parent = sabb.name
		JOIN `tabBatch` b ON b.name = sabe.batch_no
		WHERE
			sabb.item_code = %(item_code)s
			AND sabe.warehouse = %(warehouse)s
			AND sabb.storage = %(storage)s
			AND sabb.docstatus = 1
			AND sabb.is_cancelled = 0
			AND b.disabled = 0
			AND (b.expiry_date >= %(today)s OR b.expiry_date IS NULL)
		GROUP BY sabe.batch_no
		HAVING SUM(sabe.qty) > 0
		ORDER BY {order_by}
	""".format(order_by=order_by), values, as_dict=True) or []

	# Build result in same format as standard get_auto_data
	result = []
	remaining = qty
	for batch in batches:
		if remaining <= 0:
			break
		alloc = min(float(batch.qty), remaining)
		result.append({"batch_no": batch.batch_no, "qty": alloc})
		remaining -= alloc

	return result


@frappe.whitelist()
def set_bundle_storage(bundle_name, storage):
	"""Set storage on a Serial and Batch Bundle and its entries.

	Uses direct DB update to work regardless of docstatus.
	"""
	if not bundle_name:
		return
	storage = storage or ""
	frappe.db.set_value(
		"Serial and Batch Bundle", bundle_name, "storage", storage, update_modified=False
	)
	frappe.db.sql("""
		UPDATE `tabSerial and Batch Entry`
		SET storage = %(storage)s
		WHERE parent = %(bundle_name)s
	""", {"storage": storage, "bundle_name": bundle_name})


@frappe.whitelist()
def get_default_storage():
	"""Get default storage values from Manufacturing Settings."""
	doc = frappe.get_cached_doc("Manufacturing Settings")
	return {
		"wip_storage": doc.get("default_wip_storage") or "",
		"fg_storage": doc.get("default_fg_storage") or "",
	}


@frappe.whitelist()
def get_serial_no(doctype, txt, searchfield, start, page_len, filters):
	"""Get serial numbers filtered by item_code, warehouse, and storage.

	Returns (serial_no, description) where description shows batch and storage info.
	"""
	item_code = filters.get("item_code")
	warehouse = filters.get("warehouse")
	storage = filters.get("storage")

	if not item_code:
		return []

	conditions = "sn.item_code = %(item_code)s"
	values = {"item_code": item_code, "txt": f"%{txt}%"}

	if warehouse:
		conditions += " AND sn.warehouse = %(warehouse)s"
		values["warehouse"] = warehouse

	if storage:
		conditions += " AND sn.storage = %(storage)s"
		values["storage"] = storage

	return frappe.db.sql("""
		SELECT
			sn.name,
			'Qty: 1' as description
		FROM `tabSerial No` sn
		WHERE {conditions}
			AND sn.name LIKE %(txt)s
		ORDER BY sn.creation
		LIMIT %(page_len)s OFFSET %(start)s
	""".format(conditions=conditions), {**values, "page_len": cint(page_len), "start": cint(start)})


@frappe.whitelist()
def get_auto_serial_nos_with_storage(item_code, warehouse, storage, qty, based_on="FIFO"):
	"""Get auto serial numbers filtered by storage.

	Returns list of dicts with serial_no (same format as standard get_auto_data).
	"""
	qty = int(float(qty or 0))
	if not item_code or not warehouse or not storage or qty <= 0:
		return []

	order_by = "sn.creation"
	if based_on == "LIFO":
		order_by = "sn.creation DESC"
	elif based_on == "Expiry":
		order_by = "sn.amc_expiry_date"

	serial_nos = frappe.db.sql("""
		SELECT sn.name as serial_no, sn.warehouse, sn.batch_no
		FROM `tabSerial No` sn
		WHERE
			sn.item_code = %(item_code)s
			AND sn.warehouse = %(warehouse)s
			AND sn.storage = %(storage)s
		ORDER BY {order_by}
		LIMIT %(qty)s
	""".format(order_by=order_by), {
		"item_code": item_code,
		"warehouse": warehouse,
		"storage": storage,
		"qty": qty,
	}, as_dict=True) or []

	return serial_nos
