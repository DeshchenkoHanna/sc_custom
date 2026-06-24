"""
Item doctype events for the Fibery Sync module.

Real-time producer: on every Item save (create or change) put the
item_code into the Fibery Sync Queue outbox, IN THE SAME TRANSACTION as
the Item save (no network here). Delivery is done later by the scheduler
drainer (sc_custom.fibery_sync.sync.flush_queue), so a Fibery outage can
never block or fail an Item save, and a rolled-back Item never reaches
Fibery.
"""

import frappe

from sc_custom.fibery_sync.sync import enqueue_item


def enqueue_item_for_fibery(doc, method=None):
	"""on_update Item handler (fires for both insert and update)."""
	if (
		frappe.flags.in_install
		or frappe.flags.in_migrate
		or frappe.flags.in_patch
		or frappe.flags.in_import
		or getattr(frappe.flags, "in_test", False)
	):
		return

	if not doc.item_code:
		return

	try:
		enqueue_item(doc.item_code)
	except Exception:
		# Never let outbox bookkeeping break the Item save.
		frappe.log_error(title="Fibery enqueue failed")


def enqueue_item_from_sle(doc, method=None):
	"""Stock Ledger Entry after_insert: enqueue the affected Item.

	Any stock movement (Stock Entry, Delivery Note, Purchase Receipt,
	Stock Reconciliation, etc.) inserts SLE rows. We piggyback on
	``after_insert`` to refresh ``Current raw materials stock`` and
	``Total forecasted stock`` in Fibery.
	"""
	if (
		frappe.flags.in_install
		or frappe.flags.in_migrate
		or frappe.flags.in_patch
		or frappe.flags.in_import
		or getattr(frappe.flags, "in_test", False)
	):
		return

	if not doc.item_code:
		return

	try:
		enqueue_item(doc.item_code)
	except Exception:
		frappe.log_error(title="Fibery enqueue (SLE) failed")


def enqueue_items_from_mr(doc, method=None):
	"""Material Request on_submit / on_cancel / on_update_after_submit:
	enqueue every item in the MR (only for Purchase MRs).

	Affects ``Total forecasted stock`` — submitting an MR opens new
	"expected" qty, cancelling/stopping closes it. Direct DB writes that
	ERPNext does from PR/PO to ``received_qty`` / ``ordered_qty`` don't
	fire this hook, but PR always creates SLE so the SLE handler picks
	up that path; PO doesn't affect the formula at all.
	"""
	if (
		frappe.flags.in_install
		or frappe.flags.in_migrate
		or frappe.flags.in_patch
		or frappe.flags.in_import
		or getattr(frappe.flags, "in_test", False)
	):
		return

	if doc.material_request_type != "Purchase":
		return

	try:
		for row in (doc.items or []):
			if row.item_code:
				enqueue_item(row.item_code)
	except Exception:
		frappe.log_error(title="Fibery enqueue (MR) failed")
