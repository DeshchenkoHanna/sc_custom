/**
 * Stock Entry customizations for SC Custom
 * Auto-populate Storage field from Pick List when creating Stock Entry
 *
 * Mirrors ERPNext's standard behavior: simply copy Pick List Item table data,
 * no recalculation or revalidation of warehouse availability.
 */

frappe.ui.form.on('Stock Entry', {
	onload: function(frm) {
		// Only run for new documents created from Pick List
		if (!frm.doc.__islocal || !frm.doc.pick_list) {
			return;
		}

		// Only for Material Transfer for Manufacture
		if (frm.doc.purpose !== 'Material Transfer for Manufacture') {
			return;
		}

		// Check if storage already populated
		if (frm.doc.items && frm.doc.items.some(item => item.storage)) {
			return;
		}

		// Copy storage from Pick List items (same logic as ERPNext copies other fields)
		copy_storage_from_pick_list(frm);
	}
});

function copy_storage_from_pick_list(frm) {
	if (!frm.doc.pick_list || !frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	// First, get default WIP storage from Manufacturing Settings
	frappe.db.get_single_value('Manufacturing Settings', 'default_wip_storage').then(function(default_wip_storage) {
		// Then get Pick List data
		frappe.call({
			method: 'frappe.client.get',
			args: {
				doctype: 'Pick List',
				name: frm.doc.pick_list,
				fields: ['name']  // We only need locations child table
			},
			callback: function(r) {
				if (!r.message || !r.message.locations) {
					return;
				}

				let pick_list_items = r.message.locations;
				let source_updated = 0;
				let target_updated = 0;

				// Simply copy storage field from Pick List Item to Stock Entry Detail
				// Same approach as ERPNext uses for other fields in update_common_item_properties()
				// We match by idx (row position) since Stock Entry items are created in same order
				frm.doc.items.forEach(function(se_item, idx) {
					// Match by position (ERPNext creates Stock Entry items in same order as Pick List items)
					let pl_item = pick_list_items[idx];

					// Set source storage from Pick List (s_warehouse)
					if (!se_item.storage && pl_item && pl_item.storage && se_item.s_warehouse) {
						frappe.model.set_value(se_item.doctype, se_item.name, 'storage', pl_item.storage);
						source_updated++;
					}

					// Set target storage from Manufacturing Settings default (t_warehouse)
					if (!se_item.target_storage && se_item.t_warehouse && default_wip_storage) {
						frappe.model.set_value(se_item.doctype, se_item.name, 'target_storage', default_wip_storage);
						target_updated++;
					}
				});

				// Show alert if any updates were made
				let messages = [];
				if (source_updated > 0) {
					messages.push(__('{0} source storage location(s) from Pick List {1}', [source_updated, frm.doc.pick_list]));
				}
				if (target_updated > 0) {
					messages.push(__('{0} target storage location(s) from Manufacturing Settings', [target_updated]));
				}

				if (messages.length > 0) {
					frappe.show_alert({
						message: __('Copied: {0}', [messages.join(', ')]),
						indicator: 'blue'
					}, 5);

					frm.refresh_field('items');
				}
			}
		});
	});
}
