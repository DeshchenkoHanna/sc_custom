// Restrict Bank Transaction reference rows to submitted documents.
// Core erpnext filters payment_entry with docstatus != 2 (drafts allowed);
// this override tightens it to docstatus == 1. Server-side guard lives in
// sc_custom.doctype_events.bank_transaction.validate_bank_transaction.

frappe.ui.form.on("Bank Transaction", {
	setup(frm) {
		frm.set_query("payment_entry", "payment_entries", function () {
			return {
				filters: {
					docstatus: 1,
				},
			};
		});
	},
});
