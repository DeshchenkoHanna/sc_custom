/**
 * Stock Entry customizations for SC Custom
 * Auto-populate Storage field from Work Order, Pick List and Manufacturing Settings
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

		if (frm.doc.purpose === 'Material Transfer for Manufacture' || frm.doc.purpose === 'Material Transfer') {
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

/**
 * Get resolved wip_storage and fg_storage:
 * WO fields first, then Manufacturing Settings defaults as fallback.
 * Returns Promise resolving to {wip_storage, fg_storage}
 */
function get_resolved_storage(frm) {
	let promises = [
		frappe.db.get_single_value('Manufacturing Settings', 'default_wip_storage'),
		frappe.db.get_single_value('Manufacturing Settings', 'default_fg_storage')
	];

	if (frm.doc.work_order) {
		promises.push(
			frappe.db.get_value('Work Order', frm.doc.work_order, ['wip_storage', 'fg_storage'])
		);
	}

	return Promise.all(promises).then(function(results) {
		let default_wip = results[0];
		let default_fg = results[1];
		let wo_wip = '';
		let wo_fg = '';

		if (results[2] && results[2].message) {
			wo_wip = results[2].message.wip_storage || '';
			wo_fg = results[2].message.fg_storage || '';
		}

		return {
			wip_storage: wo_wip || default_wip || '',
			fg_storage: wo_fg || default_fg || ''
		};
	});
}

function copy_storage_from_pick_list(frm) {
	if (!frm.doc.pick_list || !frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	Promise.all([
		get_resolved_storage(frm),
		frappe.call({
			method: 'sc_custom.api.pick_list_storage.get_pick_list_items_storage',
			args: {
				pick_list: frm.doc.pick_list
			}
		})
	]).then(function([storage, pick_list_response]) {
		let pick_list_items = pick_list_response.message || [];
		let updated = false;

		frm.doc.items.forEach(function(se_item, idx) {
			let pl_item = pick_list_items[idx];

			// Set source storage from Pick List
			if (!se_item.storage && pl_item && pl_item.storage && se_item.s_warehouse) {
				frappe.model.set_value(se_item.doctype, se_item.name, 'storage', pl_item.storage);
				updated = true;
			}

			// Set target storage: WO wip_storage > Manufacturing Settings default
			if (!se_item.to_storage && se_item.t_warehouse && storage.wip_storage) {
				frappe.model.set_value(se_item.doctype, se_item.name, 'to_storage', storage.wip_storage);
				updated = true;
			}
		});

		if (updated) {
			frm.refresh_field('items');
		}
	});
}

function set_storage_for_manufacture(frm) {
	if (!frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	get_resolved_storage(frm).then(function(storage) {
		let updated = false;

		frm.doc.items.forEach(function(item) {
			let is_finished = item.is_finished_item || 0;

			if (is_finished) {
				// Finished item: target storage from WO fg_storage > Manufacturing Settings
				if (!item.to_storage && item.t_warehouse && storage.fg_storage) {
					frappe.model.set_value(item.doctype, item.name, 'to_storage', storage.fg_storage);
					updated = true;
				}
			} else {
				// Raw material: source storage from WO wip_storage > Manufacturing Settings
				if (!item.storage && item.s_warehouse && storage.wip_storage) {
					frappe.model.set_value(item.doctype, item.name, 'storage', storage.wip_storage);
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
		get_resolved_storage(frm)
	]).then(function([stock_response, storage]) {
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
				if (allocation.has_batch_no || allocation.has_serial_no) {
					frappe.model.set_value(se_item.doctype, se_item.name, 'use_serial_batch_fields', 1);
					updated = true;

					if (allocation.has_batch_no && allocation.batch_no) {
						frappe.model.set_value(se_item.doctype, se_item.name, 'batch_no', allocation.batch_no);
					}

					if (allocation.has_serial_no && allocation.serial_nos && allocation.serial_nos.length > 0) {
						let serial_no_str = allocation.serial_nos.join('\n');
						frappe.model.set_value(se_item.doctype, se_item.name, 'serial_no', serial_no_str);
					}
				}
			}

			// Set target storage: WO wip_storage > Manufacturing Settings default
			if (!se_item.to_storage && se_item.t_warehouse && storage.wip_storage) {
				frappe.model.set_value(se_item.doctype, se_item.name, 'to_storage', storage.wip_storage);
				updated = true;
			}
		});

		if (updated) {
			frm.refresh_field('items');
		}
	});
}
