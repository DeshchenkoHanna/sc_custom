import frappe
from frappe import _
from frappe.utils import getdate


def clean_stale_sabb(doc, method):
	"""Clean SABB link if row data no longer matches the bundle.

	When use_serial_batch_fields=1 and serial_no is set, compare
	serial numbers + warehouse + storage in the row against the linked SABB.
	If there is any difference, clear the SABB link so the standard
	on_submit creates a fresh bundle from the row fields.
	"""
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	for row in doc.locations:
		if not row.use_serial_batch_fields or not row.serial_no or not row.serial_and_batch_bundle:
			continue

		field_sns = set(get_serial_nos(row.serial_no))
		if not field_sns:
			continue

		sabb = frappe.db.get_value(
			"Serial and Batch Bundle",
			row.serial_and_batch_bundle,
			["warehouse", "storage"],
			as_dict=True,
		)
		if not sabb:
			continue

		# Compare warehouse and storage between row and SABB header
		if sabb.warehouse != row.warehouse or (sabb.storage or "") != (row.get("storage") or ""):
			row.serial_and_batch_bundle = None
			continue

		# Compare serial numbers between row and SABB entries
		sabb_sns = set(
			frappe.get_all(
				"Serial and Batch Entry",
				filters={"parent": row.serial_and_batch_bundle, "serial_no": ("is", "set")},
				pluck="serial_no",
			)
		)

		if field_sns != sabb_sns:
			row.serial_and_batch_bundle = None


def validate_pick_list(doc, method):
	"""Validate Pick List before submit.

	For documents created from 2026 onwards:
	- Storage is mandatory for all items
	- Batch No is mandatory for batch-tracked items
	- Serial No is mandatory for serial-tracked items (from row field or SABB)
	- Serial numbers must be available in the selected warehouse + storage
	"""
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	cutoff = getdate("2026-01-01")
	if getdate(doc.creation) < cutoff:
		return

	for row in doc.locations:
		if not row.item_code:
			continue

		if not row.get("storage"):
			frappe.msgprint(
				_("Row #{0}: Storage is mandatory for item {1}").format(
					row.idx, frappe.bold(row.item_code)
				),
				title=_("Missing Storage"),
				indicator="orange",
				raise_exception=True,
			)

		has_batch_no = frappe.get_cached_value("Item", row.item_code, "has_batch_no")
		if has_batch_no:
			# Get batch_no from row field or from SABB entries
			if row.batch_no:
				batch_no = row.batch_no
			elif row.serial_and_batch_bundle:
				batch_no = frappe.db.get_value(
					"Serial and Batch Entry",
					{"parent": row.serial_and_batch_bundle, "batch_no": ("is", "set")},
					"batch_no",
				)
			else:
				batch_no = None

			if not batch_no:
				frappe.msgprint(
					_("Row #{0}: Batch No is mandatory for item {1}").format(
						row.idx, frappe.bold(row.item_code)
					),
					title=_("Missing Batch No"),
					indicator="orange",
					raise_exception=True,
				)

		has_serial_no = frappe.get_cached_value("Item", row.item_code, "has_serial_no")
		if not has_serial_no:
			continue

		# Get serial numbers from row field or from SABB entries
		if row.serial_no:
			serial_nos = get_serial_nos(row.serial_no)
		elif row.serial_and_batch_bundle:
			serial_nos = frappe.get_all(
				"Serial and Batch Entry",
				filters={"parent": row.serial_and_batch_bundle, "serial_no": ("is", "set")},
				pluck="serial_no",
			)
		else:
			serial_nos = []

		if not serial_nos:
			frappe.msgprint(
				_("Row #{0}: Serial No is mandatory for item {1}").format(
					row.idx, frappe.bold(row.item_code)
				),
				title=_("Missing Serial No"),
				indicator="orange",
				raise_exception=True,
			)
			continue

		# Validate serial numbers exist in selected warehouse + storage
		if not row.get("storage"):
			continue

		valid_serial_nos = frappe.get_all(
			"Serial No",
			filters={
				"name": ("in", serial_nos),
				"warehouse": row.warehouse,
				"storage": row.storage,
			},
			pluck="name",
		)

		invalid_serial_nos = set(serial_nos) - set(valid_serial_nos)
		if invalid_serial_nos:
			frappe.msgprint(
				_("Row #{0}: Serial No {1} is not available in warehouse {2}, storage {3}.").format(
					row.idx,
					frappe.bold(", ".join(sorted(invalid_serial_nos))),
					frappe.bold(row.warehouse),
					frappe.bold(row.storage),
				),
				title=_("Incorrect Storage"),
				indicator="orange",
				raise_exception=True,
			)


def sync_sabb_storage(doc, method):
	"""Set storage on SABB header and entries from PL row after submit.

	Runs after the standard on_submit which creates SABBs via
	make_bundle_using_old_serial_batch_fields (without storage).
	"""
	for row in doc.locations:
		if not row.serial_and_batch_bundle or not row.get("storage"):
			continue

		sabb_storage = frappe.db.get_value(
			"Serial and Batch Bundle", row.serial_and_batch_bundle, "storage"
		)

		if (sabb_storage or "") != row.storage:
			frappe.db.set_value(
				"Serial and Batch Bundle",
				row.serial_and_batch_bundle,
				"storage",
				row.storage,
				update_modified=False,
			)
			frappe.db.sql("""
				UPDATE `tabSerial and Batch Entry`
				SET storage = %(storage)s
				WHERE parent = %(bundle)s
			""", {"storage": row.storage, "bundle": row.serial_and_batch_bundle})


