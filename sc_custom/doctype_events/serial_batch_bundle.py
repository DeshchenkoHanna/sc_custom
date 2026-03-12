import frappe
from frappe import _
from frappe.utils import getdate

STORAGE_MANDATORY_FROM = getdate("2026-01-01")


def validate_serial_batch_bundle(doc, method=None):
	"""Validate that storage is set for SABBs created from 2026-01-01 onwards.

	Uses posting_date if available, otherwise falls back to creation date.
	Legacy documents (pre-2026) and their amendments are exempt.
	"""
	if doc.storage:
		return

	reference_date = getdate(doc.posting_date) if doc.posting_date else getdate(doc.creation)

	if reference_date < STORAGE_MANDATORY_FROM:
		return

	frappe.throw(
		_("Storage is mandatory for Serial and Batch Bundle {0}").format(
			frappe.bold(doc.name or "")
		),
		title=_("Missing Storage"),
	)
