/**
 * Pick List customizations for SC Custom
 * Auto-populate Storage field based on available stock
 */

// Extend ERPNext's set_item_locations to add storage population
frappe.ui.form.on('Pick List', {
	onload: function(frm) {
		// Watch for changes in locations table (when Work Order mapping completes)
		frm._sc_custom_locations_length = 0;

		// Set up interval to check for new items in locations table
		if (frm._sc_custom_watch_interval) {
			clearInterval(frm._sc_custom_watch_interval);
		}

		frm._sc_custom_watch_interval = setInterval(function() {
			let current_length = frm.doc.locations ? frm.doc.locations.length : 0;

			// If locations table grew (new items added)
			if (current_length > frm._sc_custom_locations_length && current_length > 0) {
				// Check if pick_manually is enabled
				if (frm.doc.pick_manually) {
					frm._sc_custom_locations_length = current_length;
					return;
				}

				frm._sc_custom_locations_length = current_length;

				// Wait for ERPNext to finish processing
				setTimeout(function() {
					populate_storage_for_all_items(frm, false);
				}, 800);
			} else {
				frm._sc_custom_locations_length = current_length;
			}
		}, 500);
	},

	onload_post_render: function(frm) {
		// Clear interval when form is closed
		$(window).on('beforeunload', function() {
			if (frm._sc_custom_watch_interval) {
				clearInterval(frm._sc_custom_watch_interval);
			}
		});
	},

	setup: function(frm) {
		// Override set_item_locations method to add storage population
		frm.events.set_item_locations = function(frm, save) {
			// Call original ERPNext method first
			if (!(frm.doc.locations && frm.doc.locations.length)) {
				frappe.msgprint(__("Add items in the Item Locations table"));
				return;
			}

			frappe.call({
				method: "set_item_locations",
				doc: frm.doc,
				args: {
					save: save,
				},
				freeze: 1,
				freeze_message: __("Setting Item Locations..."),
				callback: function(r) {
					refresh_field("locations");

					// Populate storage after warehouses are set
					setTimeout(function() {
						populate_storage_for_all_items(frm, true); // force = always populate from "Get Item Locations"
					}, 500);
				},
			});
		};
	}
});

// Function to populate storage for all items in the table
// @param {Object} frm - Form object
// @param {Boolean} force - Force populate even if pick_manually is checked
function populate_storage_for_all_items(frm, force) {
	// Skip if pick_manually is enabled (unless forced from "Get Item Locations")
	if (frm.doc.pick_manually && !force) {
		return;
	}

	if (!frm.doc.locations || frm.doc.locations.length === 0) {
		return;
	}

	// Call server method to allocate storage for entire table
	frappe.call({
		method: 'sc_custom.api.pick_list_storage.allocate_storage_for_pick_list',
		args: {
			locations_json: JSON.stringify(frm.doc.locations)
		},
		callback: function(r) {
			if (r.message && r.message.length > 0) {
				// Apply storage to each row (reverse order for splits)
				r.message.reverse().forEach(function(allocation) {
					if (!allocation.storage) {
						return;
					}

					let row = frm.doc.locations.find(loc => loc.name === allocation.name);
					if (!row) {
						return;
					}

					// Check if row needs to be split across multiple storages
					if (allocation.split && allocation.additional_allocations && allocation.additional_allocations.length > 0) {
						// Update original row with first storage
						frappe.model.set_value(row.doctype, row.name, {
							'storage': allocation.storage,
							'stock_qty': allocation.qty,
							'qty': allocation.qty / (row.conversion_factor || 1)
						});

						// Add new rows for additional storages
						allocation.additional_allocations.forEach(function(add_alloc) {
							let new_row = frappe.model.add_child(frm.doc, 'Pick List Item', 'locations');

							frappe.model.set_value(new_row.doctype, new_row.name, {
								'item_code': row.item_code,
								'item_name': row.item_name,
								'description': row.description,
								'warehouse': row.warehouse,
								'storage': add_alloc.storage,
								'uom': row.uom,
								'stock_uom': row.stock_uom,
								'conversion_factor': row.conversion_factor,
								'stock_qty': add_alloc.qty,
								'qty': add_alloc.qty / (row.conversion_factor || 1),
								'sales_order': row.sales_order,
								'sales_order_item': row.sales_order_item,
								'material_request': row.material_request,
								'material_request_item': row.material_request_item,
								'work_order': row.work_order,
								'product_bundle_item': row.product_bundle_item,
								'batch_no': row.batch_no,
								'serial_no': row.serial_no
							});
						});

						frappe.show_alert({
							message: __('Item split across {0} storage locations', [allocation.additional_allocations.length + 1]),
							indicator: 'blue'
						}, 5);
					} else {
						// Single storage - just set it
						frappe.model.set_value(row.doctype, row.name, 'storage', allocation.storage);
					}
				});

				frm.refresh_field('locations');
			}
		}
	});
}

// Allow manual storage selection
frappe.ui.form.on('Pick List Item', {
	storage: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		// Mark that storage was manually set
		row.__manually_set_storage = true;
	}
});
