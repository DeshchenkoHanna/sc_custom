/**
 * Stock Entry customizations for SC Custom
 * Auto-populate Storage field from Pick List and Manufacturing Settings
 */

frappe.ui.form.on('Stock Entry', {
	refresh: function(frm) {
		// Only run for new documents
		if (!frm.doc.__islocal) {
			return;
		}

		// Check if storage already populated (to avoid running multiple times)
		if (frm.doc.items && frm.doc.items.some(item => item.to_storage || item.storage)) {
			return;
		}

		if (frm.doc.purpose === 'Material Transfer for Manufacture') {
			if (frm.doc.pick_list) {
				// Copy storage from Pick List items
				copy_storage_from_pick_list(frm);
			} else if (frm.doc.work_order) {
				// Get storage from available stock (FIFO) for Work Order items
				set_storage_from_work_order(frm);
			}
		} else if (frm.doc.purpose === 'Material Consumption for Manufacture' || frm.doc.purpose === 'Manufacture') {
			// Set storage from Manufacturing Settings
			set_storage_for_manufacture(frm);
		}
	}
});

function copy_storage_from_pick_list(frm) {
	if (!frm.doc.pick_list || !frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	// Get default WIP storage and Pick List data in parallel
	Promise.all([
		frappe.db.get_single_value('Manufacturing Settings', 'default_wip_storage'),
		frappe.call({
			method: 'sc_custom.api.pick_list_storage.get_pick_list_items_storage',
			args: {
				pick_list: frm.doc.pick_list
			}
		})
	]).then(function([default_wip_storage, pick_list_response]) {
		let pick_list_items = pick_list_response.message || [];
		let source_updated = 0;
		let target_updated = 0;

		frm.doc.items.forEach(function(se_item, idx) {
			let pl_item = pick_list_items[idx];

			// Set source storage from Pick List
			if (!se_item.storage && pl_item && pl_item.storage && se_item.s_warehouse) {
				frappe.model.set_value(se_item.doctype, se_item.name, 'storage', pl_item.storage);
				source_updated++;
			}

			// Set target storage from Manufacturing Settings default
			if (!se_item.to_storage && se_item.t_warehouse && default_wip_storage) {
				frappe.model.set_value(se_item.doctype, se_item.name, 'to_storage', default_wip_storage);
				target_updated++;
			}
		});

		if (source_updated > 0 || target_updated > 0) {
			frm.refresh_field('items');
		}
	});
}

function set_storage_for_manufacture(frm) {
	if (!frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	// Get default storage values from Manufacturing Settings
	Promise.all([
		frappe.db.get_single_value('Manufacturing Settings', 'default_wip_storage'),
		frappe.db.get_single_value('Manufacturing Settings', 'default_fg_storage')
	]).then(function([default_wip_storage, default_fg_storage]) {
		let updated = false;

		frm.doc.items.forEach(function(item) {
			let is_finished = item.is_finished_item || 0;

			if (is_finished) {
				// Finished item: target storage = default_fg_storage
				if (!item.to_storage && item.t_warehouse && default_fg_storage) {
					frappe.model.set_value(item.doctype, item.name, 'to_storage', default_fg_storage);
					updated = true;
				}
			} else {
				// Raw material: source storage = default_wip_storage
				if (!item.storage && item.s_warehouse && default_wip_storage) {
					frappe.model.set_value(item.doctype, item.name, 'storage', default_wip_storage);
					updated = true;
				}
			}
		});

		if (updated) {
			frm.refresh_field('items');
		}
	});
}

function set_storage_from_work_order(frm) {
	if (!frm.doc.work_order || !frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	// Prepare items data for the API call
	let items_data = frm.doc.items.map(function(item) {
		return {
			item_code: item.item_code,
			qty: item.qty || item.transfer_qty || 0
		};
	});

	// Get available stock locations (warehouse + storage + batch/serial) and default WIP storage in parallel
	// Pass work_order and purpose to prioritize Work Order source warehouses and exclude WIP warehouse
	Promise.all([
		frappe.call({
			method: 'sc_custom.api.pick_list_storage.get_available_stock_for_items',
			args: {
				items_json: JSON.stringify(items_data),
				company: frm.doc.company,
				work_order: frm.doc.work_order,
				purpose: frm.doc.purpose
			}
		}),
		frappe.db.get_single_value('Manufacturing Settings', 'default_wip_storage')
	]).then(function([stock_response, default_wip_storage]) {
		let stock_allocations = stock_response.message || [];
		let updated = false;

		frm.doc.items.forEach(function(se_item, idx) {
			let allocation = stock_allocations.find(a => a.idx === idx);

			if (allocation) {
				// Set source warehouse from available stock (FIFO/LIFO/Expiry)
				if (allocation.warehouse && allocation.warehouse !== se_item.s_warehouse) {
					frappe.model.set_value(se_item.doctype, se_item.name, 's_warehouse', allocation.warehouse);
					updated = true;
				}

				// Set source storage from available stock (FIFO/LIFO/Expiry)
				if (allocation.storage && !se_item.storage) {
					frappe.model.set_value(se_item.doctype, se_item.name, 'storage', allocation.storage);
					updated = true;
				}

				// Handle batch/serial allocation using use_serial_batch_fields
				// Serial and Batch Bundle will be created by standard ERPNext on submit
				if (allocation.has_batch_no || allocation.has_serial_no) {
					// Enable use_serial_batch_fields to use simple batch_no/serial_no fields
					frappe.model.set_value(se_item.doctype, se_item.name, 'use_serial_batch_fields', 1);
					updated = true;

					// Set batch number if available
					if (allocation.has_batch_no && allocation.batch_no) {
						frappe.model.set_value(se_item.doctype, se_item.name, 'batch_no', allocation.batch_no);
					}

					// Set serial numbers if available (newline-separated)
					if (allocation.has_serial_no && allocation.serial_nos && allocation.serial_nos.length > 0) {
						let serial_no_str = allocation.serial_nos.join('\n');
						frappe.model.set_value(se_item.doctype, se_item.name, 'serial_no', serial_no_str);
					}
				}
			}

			// Set target storage from Manufacturing Settings default
			if (!se_item.to_storage && se_item.t_warehouse && default_wip_storage) {
				frappe.model.set_value(se_item.doctype, se_item.name, 'to_storage', default_wip_storage);
				updated = true;
			}
		});

		if (updated) {
			frm.refresh_field('items');
		}
	});
}
