frappe.provide("frappe.ui.form");

// Quick Entry for Item: expose Default Supplier (lives in the item_defaults
// child table, so it is not available as a normal quick-entry field) and ask
// for confirmation when it is left empty — same rule as on the full form.
frappe.ui.form.ItemQuickEntryForm = class ItemQuickEntryForm extends (
	frappe.ui.form.QuickEntryForm
) {
	render_dialog() {
		this.docfields = this.docfields.concat([
			{
				label: __("Default Supplier"),
				fieldname: "default_supplier",
				fieldtype: "Link",
				options: "Supplier",
			},
		]);
		super.render_dialog();
	}

	insert() {
		const values = this.dialog.get_values(true) || {};
		const supplier = values.default_supplier;

		if (supplier) {
			this.dialog.doc.item_defaults = [
				{
					doctype: "Item Default",
					company: frappe.defaults.get_default("company"),
					default_supplier: supplier,
				},
			];
			return super.insert();
		}

		// Only purchase items need a default supplier (defaults to 1 on new items)
		if (!cint(this.dialog.doc.is_purchase_item)) {
			return super.insert();
		}

		return new Promise((resolve) => {
			// unlock the quick entry dialog while the warning is open
			this.dialog.working = false;
			const d = frappe.warn(
				__("No Default Supplier"),
				`<p>${__("A Default Supplier should be selected for this item.")}</p>
				<p>${__("Are you sure you want to save without a Default Supplier?")}</p>`,
				() => {
					this.dialog.working = true;
					super.insert().then(resolve);
				},
				__("Yes")
			);
			d.set_secondary_action_label(__("No"));
		});
	}
};
