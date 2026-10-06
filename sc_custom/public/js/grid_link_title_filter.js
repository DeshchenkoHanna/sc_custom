// Let the child-table filter row match Link columns by their shown title too.
//
// For doctypes with "Show Title in Link Fields" (Supplier, Customer, Project) the grid
// displays the title (e.g. the supplier name), but Frappe's filter row compares the typed
// text with the stored ID only (apps/frappe form/grid.js get_data_based_on_fieldtype), so
// searching by the name that is visible in the column finds nothing. This keeps the
// standard check and, for Link columns it did not match, also compares the title from
// the client link-title cache (filled for every row when the form loads).
//
// This patches standard Frappe behaviour at the prototype level (we must not edit
// apps/frappe). The Grid class is not exposed globally, so it is reached through the
// first ControlTable instance. Re-verify against apps/frappe form/grid.js and
// form/controls/table.js on each Frappe upgrade.

frappe.provide("frappe.ui.form");

(function () {
	const original_make = frappe.ui.form.ControlTable.prototype.make;

	frappe.ui.form.ControlTable.prototype.make = function () {
		original_make.apply(this, arguments);
		if (this.grid) {
			patch_grid(Object.getPrototypeOf(this.grid));
		}
	};

	function patch_grid(proto) {
		const original_match = proto.get_data_based_on_fieldtype;
		// method missing (renamed upstream) => keep the standard behaviour
		if (proto._sc_link_title_filter || typeof original_match !== "function") return;
		proto._sc_link_title_filter = true;

		proto.get_data_based_on_fieldtype = function (df, data, value) {
			const match = original_match.call(this, df, data, value);
			if (match || df.fieldtype !== "Link" || !data[df.fieldname]) return match;

			// value is already lower-cased by get_filtered_data
			const title = frappe.utils.get_link_title(df.options, data[df.fieldname]);
			if (title && String(title).toLowerCase().includes(value)) return data;
		};
	}
})();
