// ERPNext v16 (PR #53391) removed the supplier prompt that appeared when creating a
// Purchase Order from a Material Request, along with the server-side item filtering by
// default supplier. This restores the v15 behaviour for Purchase-type Material Requests:
// the "Create > Purchase Order" button asks for a supplier and the resulting PO contains
// only the items whose Item Default supplier matches (blank supplier => all items).

frappe.ui.form.on('Material Request', {
	refresh: function (frm) {
		if (frm.doc.docstatus === 1 && frm.doc.material_request_type === 'Purchase') {
			// Replace the standard all-items "Purchase Order" button with the supplier-prompt one.
			// (Our refresh runs after erpnext's, which has already added the standard button.)
			frm.remove_custom_button('Purchase Order', 'Create');
			frm.add_custom_button(
				__('Purchase Order'),
				function () {
					sc_make_purchase_order_with_supplier(frm);
				},
				__('Create')
			);
		}
	},
});

// Show the item's default supplier / supplier part no as soon as it is picked, without
// waiting for a save (the server recomputes both columns on every save and submit).
frappe.ui.form.on('Material Request Item', {
	item_code: function (frm, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);

		if (!row.item_code || frm.doc.material_request_type !== 'Purchase') {
			frappe.model.set_value(cdt, cdn, 'custom_default_supplier', null);
			frappe.model.set_value(cdt, cdn, 'custom_supplier_part_no', null);
			return;
		}

		frappe.call({
			method: 'sc_custom.api.material_request.get_default_supplier_info',
			args: { item_code: row.item_code, company: frm.doc.company },
			callback: function (r) {
				// The row may have been deleted or repointed while the request was in flight.
				let current = locals[cdt] && locals[cdt][cdn];
				if (!r.message || !current || current.item_code !== row.item_code) return;
				frappe.model.set_value(cdt, cdn, 'custom_default_supplier', r.message.default_supplier);
				frappe.model.set_value(cdt, cdn, 'custom_supplier_part_no', r.message.supplier_part_no);
			},
		});
	},
});

function sc_make_purchase_order_with_supplier(frm) {
	frappe.prompt(
		{
			label: __('For Default Supplier (Optional)'),
			fieldname: 'default_supplier',
			fieldtype: 'Link',
			options: 'Supplier',
			description: __(
				'Select a Supplier from the Default Suppliers of the items below. On selection, a Purchase Order will be made against items belonging to the selected Supplier only.'
			),
			get_query: function () {
				return {
					query: 'sc_custom.api.material_request.get_default_supplier_query',
					filters: { doc: frm.doc.name },
				};
			},
		},
		function (values) {
			frappe.model.open_mapped_doc({
				method: 'sc_custom.api.material_request.make_purchase_order_with_supplier',
				frm: frm,
				args: { default_supplier: values.default_supplier },
				run_link_triggers: true,
			});
		},
		__('Enter Supplier'),
		__('Create')
	);
}
