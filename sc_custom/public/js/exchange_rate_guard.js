// Foreign-currency purchase documents kept at exchange rate 1.00 (FEAT-218).
//
// ERPNext skips the exchange-rate lookup when the currency changes on a document
// that was just created from another one (transaction.js, `load_after_mapping`).
// A PO made from a Material Request, or a PR/PI made from such a PO, therefore
// kept rate 1.00 in EUR/USD/GBP and its amounts went into CHF one for one.
//
// Behaviour (new documents only, never blocks saving):
//   - when a foreign currency still has rate 1.00 on load or after a currency
//     change, fetch the rate for the document date and set it;
//   - only the rate changes: on a mapped document ERPNext then recalculates the
//     totals without repricing the items, so the foreign item rates stay as
//     they were in the source document.
// The server-side warning (doctype_events/exchange_rate.py) covers what is left,
// e.g. a rate set back to 1.00 by hand or a saved draft.

(function () {
	const DOCTYPES = ["Purchase Order", "Purchase Receipt", "Purchase Invoice"];

	function has_foreign_rate_one(frm, company_currency) {
		return (
			frm.doc.currency &&
			frm.doc.currency !== company_currency &&
			flt(frm.doc.conversion_rate) === 1
		);
	}

	function fix_foreign_rate_one(frm) {
		if (!frm.is_new() || frm.doc.docstatus !== 0) return;
		const company_currency = erpnext.get_currency(frm.doc.company);
		if (!has_foreign_rate_one(frm, company_currency)) return;

		frappe
			.xcall("erpnext.setup.utils.get_exchange_rate", {
				from_currency: frm.doc.currency,
				to_currency: company_currency,
				transaction_date: frm.doc.posting_date || frm.doc.transaction_date,
				args: "for_buying",
			})
			.then((rate) => {
				// Currency or rate may have changed while the request was running.
				if (flt(rate) && flt(rate) !== 1 && has_foreign_rate_one(frm, company_currency)) {
					frm.set_value("conversion_rate", rate);
				}
			});
	}

	// Attached to all three doctypes → evaluated whenever any is opened.
	// frappe.ui.form.on does not dedupe, so guard registration globally.
	window._sc_exchange_rate_guard_registered = window._sc_exchange_rate_guard_registered || {};

	DOCTYPES.forEach((doctype) => {
		if (window._sc_exchange_rate_guard_registered[doctype]) return;
		window._sc_exchange_rate_guard_registered[doctype] = true;
		frappe.ui.form.on(doctype, {
			onload_post_render: fix_foreign_rate_one,
			currency: fix_foreign_rate_one,
		});
	});
})();
