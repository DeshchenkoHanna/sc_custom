// Per-supplier rounded total for purchase documents.
//
// Company default disables rounding (Global Defaults + system-generated
// property setters keep disable_rounded_total = 1), but suppliers flagged
// with Supplier.custom_enforce_rounding issue rounded invoices.
//
// Behaviour:
//   - disable_rounded_total is inherited: from the source document (PR/PO/SQ
//     references in items) for mapped documents, otherwise from the supplier
//     flag. The core buying controller overwrites the mapped value with the
//     doctype default on every new form load, so it is restored here.
//   - The user may override the value manually; nothing is blocked.
//   - On submit, if the value disagrees with the supplier flag or with any
//     source document, one confirmation dialog is shown; declining aborts
//     the submit (frappe.validated = false, awaited by form.js savesubmit).

(function () {
	const DOCTYPES = [
		"Purchase Order",
		"Purchase Invoice",
		"Purchase Receipt",
		"Supplier Quotation",
	];

	// Nearest source first: PI from PR beats the PR's own PO reference.
	const SOURCE_FIELDS = [
		["Purchase Receipt", "purchase_receipt"],
		["Purchase Order", "purchase_order"],
		["Supplier Quotation", "supplier_quotation"],
	];

	function baseline_disable(doctype) {
		// same resolution as the core buying controller uses for new docs
		const df = frappe.meta.get_docfield(doctype, "disable_rounded_total");
		return cint(df && df.default) || cint(frappe.sys_defaults.disable_rounded_total);
	}

	async function supplier_enforces_rounding(supplier) {
		if (!supplier) return false;
		const r = await frappe.db.get_value("Supplier", supplier, "custom_enforce_rounding");
		return cint(r.message && r.message.custom_enforce_rounding) === 1;
	}

	function get_source_refs(frm) {
		const refs = [];
		for (const [doctype, fieldname] of SOURCE_FIELDS) {
			const names = new Set();
			(frm.doc.items || []).forEach((row) => {
				if (row[fieldname]) names.add(row[fieldname]);
			});
			if (names.size) refs.push({ doctype, names: [...names] });
		}
		return refs;
	}

	async function fetch_source_flags(frm) {
		const out = [];
		for (const ref of get_source_refs(frm)) {
			const rows = await frappe.db.get_list(ref.doctype, {
				filters: { name: ["in", ref.names] },
				fields: ["name", "disable_rounded_total"],
				limit: ref.names.length,
			});
			rows.forEach((d) =>
				out.push({
					doctype: ref.doctype,
					name: d.name,
					disable: cint(d.disable_rounded_total),
				})
			);
		}
		return out;
	}

	async function apply_value(frm, value, reason) {
		if (cint(frm.doc.disable_rounded_total) === cint(value)) return;
		// set_value fires our disable_rounded_total handler, which recalculates
		await frm.set_value("disable_rounded_total", cint(value));
		frappe.show_alert({ message: reason, indicator: "blue" });
	}

	async function inherit_on_load(frm) {
		// once per new document: restore/derive the value the core buying
		// controller overwrote with the doctype default during onload.
		if (!frm.doc.__islocal) return;
		if (frm._sc_rounding_applied_for === frm.doc.name) return;

		// A mapped draft (Create > Purchase Invoice) fires refresh several times
		// while it is being populated; the first one may run before items/supplier
		// are attached. Only lock the guard once we actually have something to
		// derive from, so a later refresh can still apply it.
		const sources = await fetch_source_flags(frm);
		if (sources.length) {
			frm._sc_rounding_applied_for = frm.doc.name;
			const src = sources[0];
			await apply_value(
				frm,
				src.disable,
				src.disable
					? __("Rounded total disabled — inherited from {0} {1}.", [
							__(src.doctype),
							src.name,
					  ])
					: __("Rounded total enabled — inherited from {0} {1}.", [
							__(src.doctype),
							src.name,
					  ])
			);
			return;
		}

		if (frm.doc.supplier) {
			frm._sc_rounding_applied_for = frm.doc.name;
			const enforce = await supplier_enforces_rounding(frm.doc.supplier);
			await apply_value(
				frm,
				enforce ? 0 : baseline_disable(frm.doc.doctype),
				enforce
					? __("Rounded total enabled — supplier {0} enforces rounding.", [frm.doc.supplier])
					: __("Rounded total set to company default.")
			);
		}
		// nothing to derive yet — leave the guard unset for a later refresh
	}

	async function on_supplier_change(frm) {
		if (!frm.doc.supplier) return;
		// mapped drafts follow their source document, not the supplier
		if (get_source_refs(frm).length) return;
		frm._sc_rounding_applied_for = frm.doc.name;
		const enforce = await supplier_enforces_rounding(frm.doc.supplier);
		const target = enforce ? 0 : baseline_disable(frm.doc.doctype);
		await apply_value(
			frm,
			target,
			enforce
				? __("Rounded total enabled — supplier {0} enforces rounding.", [frm.doc.supplier])
				: __("Rounded total reset to company default.")
		);
	}

	async function confirm_on_submit(frm) {
		const doc_disable = cint(frm.doc.disable_rounded_total);
		const problems = [];

		if (frm.doc.supplier) {
			const enforce = await supplier_enforces_rounding(frm.doc.supplier);
			const expected = enforce ? 0 : baseline_disable(frm.doc.doctype);
			if (doc_disable !== expected) {
				problems.push(
					doc_disable
						? __(
								"Rounded Total is disabled in this document, but supplier {0} uses rounding.",
								[frm.doc.supplier]
						  )
						: __(
								"Rounded Total is enabled in this document, but supplier {0} does not use rounding.",
								[frm.doc.supplier]
						  )
				);
			}
		}

		(await fetch_source_flags(frm)).forEach((src) => {
			if (src.disable !== doc_disable) {
				problems.push(
					src.disable
						? __("The {0} {1} has rounding disabled.", [__(src.doctype), src.name])
						: __("The {0} {1} has rounding enabled.", [__(src.doctype), src.name])
				);
			}
		});

		if (!problems.length) return;

		await new Promise((resolve) => {
			// Default to blocking; only an explicit "Submit Anyway" clears it.
			// onhide covers Cancel / Escape / backdrop close so the promise
			// always settles and the submit never hangs.
			frappe.validated = false;
			const d = frappe.warn(
				__("Rounded Total Mismatch"),
				`<p>${__("The “Rounded Total” setting is inconsistent:")}</p>
				<ul><li>${problems.join("</li><li>")}</li></ul>
				<p>${__("Do you want to submit this document anyway?")}</p>`,
				() => {
					frappe.validated = true;
					resolve();
				},
				__("Submit Anyway")
			);
			d.set_secondary_action_label(__("Cancel"));
			d.onhide = () => resolve();
		});
	}

	// This file is attached to all four doctypes, so it is evaluated (and the
	// handlers re-registered) whenever any of them is opened. frappe.ui.form.on
	// does not dedupe — it pushes onto a global, session-lived handler list — so
	// without a guard before_submit would fire once per form previously opened.
	window._sc_rounding_registered = window._sc_rounding_registered || {};

	DOCTYPES.forEach((doctype) => {
		if (window._sc_rounding_registered[doctype]) return;
		window._sc_rounding_registered[doctype] = true;
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				inherit_on_load(frm);
			},
			supplier(frm) {
				return on_supplier_change(frm);
			},
			disable_rounded_total(frm) {
				// core has no handler for this checkbox — without a recalc the
				// visible totals stay stale until save
				if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
					frm.cscript.calculate_taxes_and_totals();
				}
			},
			before_submit(frm) {
				return confirm_on_submit(frm);
			},
		});
	});
})();
