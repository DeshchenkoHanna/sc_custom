// Per-supplier rounded total for purchase documents.
//
// Company default disables rounding (Global Defaults + system-generated
// property setters keep disable_rounded_total = 1), but suppliers flagged
// with Supplier.custom_enforce_rounding issue rounded invoices.
//
// Behaviour:
//   - disable_rounded_total is inherited: from the source document (PR/PO/SQ
//     references in items) for mapped documents, otherwise from the supplier
//     flag. Core buying.js overwrites the value with the doctype default during
//     load; we re-assert the derived value the instant that happens (immediate
//     display) and also enforce it at validate (race-free guarantee at save).
//   - The user may override manually; a real click on the checkbox is detected
//     via a capture-phase DOM listener (programmatic set_value does not fire it),
//     and once the user has touched it we stop re-asserting for that document.
//   - On submit, if the value disagrees with the supplier flag or a source
//     document, one confirmation dialog is shown; declining aborts the submit.

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

	// Derive the value this document SHOULD have: from the nearest source
	// document if it was created against one, otherwise from the supplier flag.
	// Returns {value, reason} or null when there is nothing to derive yet.
	async function derive_desired(frm) {
		const sources = await fetch_source_flags(frm);
		if (sources.length) {
			const src = sources[0];
			return {
				value: cint(src.disable),
				reason: src.disable
					? __("Rounded total disabled — inherited from {0} {1}.", [__(src.doctype), src.name])
					: __("Rounded total enabled — inherited from {0} {1}.", [__(src.doctype), src.name]),
			};
		}
		if (frm.doc.supplier) {
			const enforce = await supplier_enforces_rounding(frm.doc.supplier);
			return {
				value: enforce ? 0 : baseline_disable(frm.doc.doctype),
				reason: enforce
					? __("Rounded total enabled — supplier {0} enforces rounding.", [frm.doc.supplier])
					: __("Rounded total set to company default."),
			};
		}
		return null;
	}

	// Programmatic set that does NOT count as a user override.
	async function set_disable(frm, value) {
		if (cint(frm.doc.disable_rounded_total) === cint(value)) return false;
		frm._sc_programmatic = true;
		try {
			await frm.set_value("disable_rounded_total", cint(value));
		} finally {
			frm._sc_programmatic = false;
		}
		return true;
	}

	// Detect a genuine user click on the checkbox. Programmatic set_value updates
	// the input via .prop() and does NOT fire the DOM change event, so this only
	// fires on real user interaction. Bound in the CAPTURE phase so it runs before
	// frappe's own (bubble-phase) handler — that way _sc_user_touched is set before
	// the form's disable_rounded_total handler decides whether to re-assert, and a
	// manual override is never overwritten.
	function bind_user_click(frm) {
		if (frm._sc_click_bound) return;
		const field = frm.fields_dict && frm.fields_dict.disable_rounded_total;
		const input = field && field.$input && field.$input[0];
		if (!input) return;
		frm._sc_click_bound = true;
		input.addEventListener(
			"change",
			() => {
				frm._sc_user_touched = frm.doc.name;
			},
			true
		);
	}

	// refresh: derive the desired value and apply it for immediate display. Core
	// clobbers it back to the default shortly after — the disable_rounded_total
	// handler below re-asserts the desired value the instant that happens.
	async function inherit_on_load(frm) {
		if (!frm.doc.__islocal) return;
		if (frm._sc_user_touched === frm.doc.name) return;

		const desired = await derive_desired(frm);
		if (!desired) return;
		frm._sc_desired = { name: frm.doc.name, value: cint(desired.value), reason: desired.reason };
		if (await set_disable(frm, desired.value)) {
			frappe.show_alert({ message: desired.reason, indicator: "blue" });
		}
	}

	// validate: race-free guarantee at save. Enforces the derived value unless the
	// user explicitly clicked the checkbox for this document.
	async function enforce_on_validate(frm) {
		if (!frm.doc.__islocal) return;
		if (frm._sc_user_touched === frm.doc.name) return;
		const desired =
			frm._sc_desired && frm._sc_desired.name === frm.doc.name
				? frm._sc_desired
				: await derive_desired(frm);
		if (!desired) return;
		await set_disable(frm, desired.value);
	}

	async function on_supplier_change(frm) {
		if (!frm.doc.supplier) return;
		if (get_source_refs(frm).length) return; // mapped drafts follow their source
		frm._sc_user_touched = null; // supplier change re-derives
		const enforce = await supplier_enforces_rounding(frm.doc.supplier);
		const value = enforce ? 0 : baseline_disable(frm.doc.doctype);
		frm._sc_desired = { name: frm.doc.name, value: cint(value) };
		if (await set_disable(frm, value)) {
			frappe.show_alert({
				message: enforce
					? __("Rounded total enabled — supplier {0} enforces rounding.", [frm.doc.supplier])
					: __("Rounded total reset to company default."),
				indicator: "blue",
			});
		}
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
						? __("Rounded Total is disabled in this document, but supplier {0} uses rounding.", [frm.doc.supplier])
						: __("Rounded Total is enabled in this document, but supplier {0} does not use rounding.", [frm.doc.supplier])
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

	// Attached to all four doctypes → evaluated whenever any is opened.
	// frappe.ui.form.on does not dedupe, so guard registration globally.
	window._sc_rounding_registered = window._sc_rounding_registered || {};

	DOCTYPES.forEach((doctype) => {
		if (window._sc_rounding_registered[doctype]) return;
		window._sc_rounding_registered[doctype] = true;
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				bind_user_click(frm);
				inherit_on_load(frm);
			},
			onload_post_render(frm) {
				bind_user_click(frm);
			},
			supplier(frm) {
				return on_supplier_change(frm);
			},
			disable_rounded_total(frm) {
				if (frm.cscript && frm.cscript.calculate_taxes_and_totals) {
					frm.cscript.calculate_taxes_and_totals();
				}
				// Re-assert the derived value the instant core's load-time default
				// clobbers it (something other than us or the user changed it away
				// from the derived value on a new document).
				if (
					frm.doc.__islocal &&
					!frm._sc_programmatic &&
					frm._sc_user_touched !== frm.doc.name &&
					frm._sc_desired &&
					frm._sc_desired.name === frm.doc.name &&
					cint(frm.doc.disable_rounded_total) !== cint(frm._sc_desired.value)
				) {
					set_disable(frm, frm._sc_desired.value);
				}
			},
			validate(frm) {
				return enforce_on_validate(frm);
			},
			before_submit(frm) {
				return confirm_on_submit(frm);
			},
		});
	});
})();
