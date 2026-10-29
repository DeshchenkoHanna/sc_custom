// Customer / Supplier: when the Billing Currency is chosen, fill the Default Accounts row for the
// company (Default Account + Default Advance Account of that currency). The server hook
// sc_custom.doctype_events.party_currency does the same on save and checks the currency against
// the accounting history; this only shows the result before saving.
// Default Price List: an empty one gets the buying (Supplier) / selling (Customer) Price List of
// that currency, a price list in another currency gets a warning (same rule as the server hook).

["Customer", "Supplier"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		default_currency(frm) {
			fill_default_accounts(frm);
			fill_default_price_list(frm);
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

function fill_default_price_list(frm) {
	const currency = frm.doc.default_currency;
	if (!currency) return;
	if (!frm.doc.default_price_list) {
		frappe.xcall("sc_custom.doctype_events.party_currency.get_party_price_list", {
			party_type: frm.doctype,
			currency: currency,
		}).then((price_list) => price_list && frm.set_value("default_price_list", price_list));
		return;
	}
	frappe.db.get_value("Price List", frm.doc.default_price_list, "currency").then(({ message }) => {
		if (message && message.currency && message.currency !== currency) {
			frappe.show_alert({
				message: __("Default Price List {0} is in {1}, the Billing Currency is {2}.", [
					frm.doc.default_price_list,
					message.currency,
					currency,
				]),
				indicator: "orange",
			});
		}
	});
}
