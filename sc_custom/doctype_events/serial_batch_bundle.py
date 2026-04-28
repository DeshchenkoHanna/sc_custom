import frappe
from frappe import _
from frappe.utils import getdate

STORAGE_MANDATORY_FROM = getdate("2026-01-01")


def validate_serial_batch_bundle(doc, method=None):
	"""Validate that storage is set for SABBs created from 2026-01-01 onwards.

	Uses posting_datetime if available, otherwise falls back to creation date.
	Legacy documents (pre-2026) and their amendments are exempt.

	Only enforced on submit (docstatus==1). Drafts are exempt because parent vouchers
	(e.g. Subcontracting Receipt) auto-create SABBs through SerialBatchCreation during
	validate without storage; the parent's submit-time logic injects storage afterwards.
	Validating drafts would block parent save before storage gets populated.

	Final enforcement of storage at the SCR row level lives in validate_storage_fields_scr.
	"""
	if doc.storage:
		return

	if doc.docstatus != 1:
		return

	reference_date = getdate(doc.posting_datetime) if doc.posting_datetime else getdate(doc.creation)

	if reference_date < STORAGE_MANDATORY_FROM:
		return

	frappe.throw(
		_("Storage is mandatory for Serial and Batch Bundle {0}").format(
			frappe.bold(doc.name or "")
		),
		title=_("Missing Storage"),
	)
