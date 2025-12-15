/**
 * Delivery Note customizations for SC Custom
 * Auto-populate default_storage field based on available stock
 */

frappe.ui.form.on('Delivery Note', {
	refresh: function(frm) {
		// Populate default_storage for items when form loads
		if (frm.doc.items && frm.doc.items.length > 0) {
			populate_default_storage_for_items(frm);
		}
	}
});

frappe.ui.form.on('Delivery Note Item', {
	item_code: function(frm, cdt, cdn) {
		// When item is added or changed, populate default_storage
		let row = locals[cdt][cdn];
		if (row.item_code) {
			// Wait for warehouse to be set by standard logic
			setTimeout(function() {
				populate_default_storage_for_row(frm, row);
			}, 500);
		}
	},

	warehouse: function(frm, cdt, cdn) {
		// When warehouse changes, update default_storage
		let row = locals[cdt][cdn];
		if (row.item_code && row.warehouse) {
			populate_default_storage_for_row(frm, row);
		}
	}
});

/**
 * Populate default_storage for all items in the table
 */
function populate_default_storage_for_items(frm) {
	if (!frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	// Filter items that need default_storage populated
	let items_to_check = frm.doc.items.filter(item =>
		item.item_code && item.warehouse && !item.default_storage
	);

	if (items_to_check.length === 0) {
		return;
	}

	// Prepare items data for API call
	let items_data = items_to_check.map(item => ({
		item_code: item.item_code,
		warehouse: item.warehouse,
		row_name: item.name
	}));

	frappe.call({
		method: 'sc_custom.api.delivery_note_storage.get_default_storage_for_items',
		args: {
			items_json: JSON.stringify(items_data)
		},
		callback: function(r) {
			if (r.message) {
				let updated = false;
				r.message.forEach(function(result) {
					if (result.default_storage) {
						let row = frm.doc.items.find(item => item.name === result.row_name);
						if (row && !row.default_storage) {
							frappe.model.set_value(row.doctype, row.name, 'default_storage', result.default_storage);
							updated = true;
						}
					}
				});

				if (updated) {
					frm.refresh_field('items');
				}
			}
		}
	});
}

/**
 * Populate default_storage for a single row
 */
function populate_default_storage_for_row(frm, row) {
	if (!row.item_code || !row.warehouse) {
		return;
	}

	frappe.call({
		method: 'sc_custom.api.delivery_note_storage.get_default_storage_for_item',
		args: {
			item_code: row.item_code,
			warehouse: row.warehouse
		},
		callback: function(r) {
			if (r.message) {
				frappe.model.set_value(row.doctype, row.name, 'default_storage', r.message);
			}
		}
	});
}
