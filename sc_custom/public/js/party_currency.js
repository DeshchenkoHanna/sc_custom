// Customer / Supplier: when the Billing Currency is chosen, fill the Default Accounts row for the
// company (Default Account + Default Advance Account of that currency). The server hook
// sc_custom.doctype_events.party_currency does the same on save and checks the currency against
// the accounting history; this only shows the result before saving.

["Customer", "Supplier"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		default_currency(frm) {
			fill_default_accounts(frm);
		},
	});
});

function fill_default_accounts(frm) {
	const company = frappe.defaults.get_default("company");
	const currency = frm.doc.default_currency;
	if (!company || !currency) return;
	frappe.xcall("sc_custom.doctype_events.party_currency.get_default_accounts", {
		party_type: frm.doctype,
		currency: currency,
		company: company,
	}).then((accounts) => {
		if (!accounts || !accounts.account) {
			frappe.show_alert({
				message: __("No single {0} account found for {1}; set the Default Accounts by hand.", [
					frm.doctype === "Customer" ? __("Receivable") : __("Payable"),
					currency,
				]),
				indicator: "orange",
			});
			return;
		}
		let row = (frm.doc.accounts || []).find((a) => a.company === company);
		if (!row) {
			row = frm.add_child("accounts", { company: company });
		}
		if (!row.account || row.account === accounts.account) {
			row.account = accounts.account;
			row.advance_account = row.advance_account || accounts.advance_account;
			frm.refresh_field("accounts");
		} else {
			frappe.show_alert({
				message: __("Default Account {0} is in another currency than {1}; check the Default Accounts table.", [
					row.account,
					currency,
				]),
				indicator: "orange",
			});
		}
	});
}
