import frappe
from frappe import _


def validate_bank_transaction(doc, method=None):
	"""Allow only submitted documents in the Bank Transaction reference table.

	Core erpnext only excludes cancelled documents in the UI filter and has no
	server-side check, so drafts can be linked by pasting a name or via API.
	"""
	for row in doc.get("payment_entries") or []:
		if not (row.payment_document and row.payment_entry):
			continue

		docstatus = frappe.db.get_value(row.payment_document, row.payment_entry, "docstatus")
		if docstatus != 1:
			frappe.throw(
				_("Row #{0}: {1} {2} is not submitted. Only submitted documents can be reconciled.").format(
					row.idx, _(row.payment_document), frappe.bold(row.payment_entry)
				),
				title=_("Unsubmitted Reference"),
			)
