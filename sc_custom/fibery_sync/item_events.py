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
