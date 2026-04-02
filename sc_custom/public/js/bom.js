frappe.ui.form.on("BOM Item", {
	item_code(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (!row.item_code) return;

		frappe.db.get_value("Item", row.item_code, "custom_do_not_explode_default", (r) => {
			if (r && r.custom_do_not_explode_default) {
				frappe.model.set_value(cdt, cdn, "do_not_explode", 1);
			}
		});
	}
});
