/**
 * Stock Entry customizations for SC Custom
 * Auto-populate Storage field from Work Order, Pick List and Company settings
 */

frappe.ui.form.on('Stock Entry', {
	setup: function(frm) {
		// Storage query: filter by item_code + s_warehouse, show only storages with stock
		frm.set_query("storage", "items", (frm, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: "sc_custom.api.queries.get_storage",
				filters: {
					item_code: row.item_code,
					warehouse: row.s_warehouse,
				},
			};
		});

		// Batch query: filter by item_code + warehouse + storage
		frm.set_query("batch_no", "items", (frm, cdt, cdn) => {
			const row = locals[cdt][cdn];
			// In set_query callbacks Frappe passes (doc, cdt, cdn) — frm IS the doc
			const is_inward = frm.purpose === "Material Receipt" ||
				(frm.purpose === "Manufacture" && row.is_finished_item);

			if (is_inward) {
				// For inward: show all batches including new ones not yet in stock
				return {
					query: "erpnext.controllers.queries.get_batch_no",
					filters: {
						item_code: row.item_code,
						warehouse: row.t_warehouse,
						is_inward: 1,
					},
				};
			}

			// For outward: filter by warehouse + storage when storage is selected
			let filters = {
				item_code: row.item_code,
				warehouse: row.s_warehouse || row.t_warehouse,
			};
			if (row.storage) {
				filters.storage = row.storage;
				return {
					query: "sc_custom.api.queries.get_batch_no",
					filters: filters,
				};
			}
			return {
				query: "erpnext.controllers.queries.get_batch_no",
				filters: filters,
			};
		});

		// Fetch Stock: limit warehouse picker to non-group warehouses of the company
		frm.set_query("custom_fetch_warehouse", (frm) => {
			return {
				filters: {
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});
	},

	custom_fetch_stock_btn: function(frm) {
		fetch_stock(frm);
	},

	before_submit: function(frm) {
		if (frm._sc_skip_pl_warning || !frm.doc.pick_list) return;

		frappe.validated = false;
		frappe.call({
			method: "sc_custom.doctype_events.stock_entry.check_ste_pl_differences",
			args: { ste_name: frm.doc.name },
			callback: (r) => {
				if (r.message && r.message.length) {
					let rows = r.message.map(d =>
						`<li><b>${__("Row")} #${d.idx}</b> (${d.item_code}): ${d.diffs.join(", ")}</li>`
					).join("");
					let msg = `<p>${__("The following items differ from the Pick List")}:</p><ul>${rows}</ul>`
						+ `<p>${__("Are you sure you want to submit?")}</p>`;

					frappe.confirm(msg, () => {
						frm._sc_skip_pl_warning = true;
						frappe.validated = true;
						frm.save('Submit');
					});
				} else {
					frm._sc_skip_pl_warning = true;
					frappe.validated = true;
					frm.save('Submit');
				}
			},
		});
	},

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
			// Set storage from Company settings
			set_storage_for_manufacture(frm);
		} else if (frm.doc.purpose === 'Send to Subcontractor' && frm.doc.subcontracting_order) {
			set_storage_from_subcontracting_order(frm);
		}
	}
});

/**
 * Fetch Stock: load all items on hand at the selected Warehouse + Storage
 * into the Items table as source (consumed) rows. Batches/serials are
 * pre-selected from that storage via the row serial/batch fields.
 */
function fetch_stock(frm) {
	// Read from the controls directly (with frm.doc fallback): clicking the
	// button can fire before an adjacent Link field commits its selected value
	// to frm.doc, which would otherwise read as empty.
	const read_field = (fieldname) => {
		const ctrl = frm.fields_dict[fieldname];
		const val = ctrl && ctrl.get_value ? ctrl.get_value() : null;
		return val || frm.doc[fieldname];
	};

	const warehouse = read_field("custom_fetch_warehouse");
	const storage = read_field("custom_fetch_storage");

	if (!warehouse || !storage) {
		frappe.msgprint(__("Please select both Warehouse and Storage."));
		return;
	}

	const load = (replace) => {
		frappe.call({
			method: "sc_custom.api.fetch_stock.get_stock_items_by_storage",
			args: { warehouse, storage, parent_doc: frm.doc },
			freeze: true,
			freeze_message: __("Fetching stock…"),
			callback: (r) => {
				const items = r.message || [];
				if (!items.length) {
					frappe.msgprint(__("No stock found at the selected Warehouse and Storage."));
					return;
				}

				if (replace) {
					frm.clear_table("items");
				}

				const existing = new Set(
					(frm.doc.items || []).map((d) => d.item_code)
				);

				let added = 0;
				items.forEach((data) => {
					if (!replace && existing.has(data.item_code)) return;
					const row = frm.add_child("items", data);
					// add_child ignores fields not in the meta on some versions;
					// set the key populated fields explicitly to be safe.
					Object.assign(row, data);
					added += 1;
				});

				frm.refresh_field("items");

				// Recompute rate, amount and availability exactly as ERPNext
				// does automatically (the "Update Rate and Availability" flow).
				// This resolves outgoing rates against each row's batch/bundle.
				frm.call({ method: "get_stock_and_rate", doc: frm.doc }).then(() => {
					frm.refresh_field("items");
					frm.dirty();
					frappe.show_alert({
						message: __("Fetched {0} item(s).", [added]),
						indicator: "green",
					});
				});
			},
		});
	};

	if (frm.doc.items && frm.doc.items.length) {
		const d = new frappe.ui.Dialog({
			title: __("Items table is not empty"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "msg",
					options: `<p>${__("Replace the existing rows, or append the fetched items (skipping items already present)?")}</p>`,
				},
			],
			primary_action_label: __("Replace"),
			primary_action: () => {
				d.hide();
				load(true);
			},
			secondary_action_label: __("Append"),
			secondary_action: () => {
				d.hide();
				load(false);
			},
		});
		d.show();
	} else {
		load(true);
	}
}

/**
 * Get resolved wip_storage and fg_storage:
 * WO fields first, then Company defaults as fallback.
 * Returns Promise resolving to {wip_storage, fg_storage}
 */
function get_resolved_storage(frm) {
	let company = frm.doc.company || frappe.defaults.get_default('company');
	let promises = [];

	if (company) {
		promises.push(
			frappe.db.get_value('Company', company, ['default_wip_storage', 'default_fg_storage'])
		);
	} else {
		promises.push(Promise.resolve(null));
	}

	if (frm.doc.work_order) {
		promises.push(
			frappe.db.get_value('Work Order', frm.doc.work_order, ['wip_storage', 'fg_storage'])
		);
	}

	return Promise.all(promises).then(function(results) {
		let default_wip = '';
		let default_fg = '';
		if (results[0] && results[0].message) {
			default_wip = results[0].message.default_wip_storage || '';
			default_fg = results[0].message.default_fg_storage || '';
		}

		let wo_wip = '';
		let wo_fg = '';
		if (results[1] && results[1].message) {
			wo_wip = results[1].message.wip_storage || '';
			wo_fg = results[1].message.fg_storage || '';
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

			// Set target storage: WO wip_storage > Company default
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

	let promises = [get_resolved_storage(frm)];

	// Fetch transfer STE inward data if WO exists
	if (frm.doc.work_order) {
		promises.push(
			frappe.call({
				method: 'sc_custom.doctype_events.stock_entry.get_transfer_inward_items',
				args: { work_order: frm.doc.work_order }
			})
		);
	}

	Promise.all(promises).then(function(results) {
		let storage = results[0];
		let transfer_items = (results[1] && results[1].message) || {};
		let updated = false;

		frm.doc.items.forEach(function(item) {
			let is_finished = item.is_finished_item || 0;

			if (is_finished) {
				// Finished item: target storage from WO fg_storage > Company default
				if (!item.to_storage && item.t_warehouse && storage.fg_storage) {
					frappe.model.set_value(item.doctype, item.name, 'to_storage', storage.fg_storage);
					updated = true;
				}
			} else {
				let t_item = transfer_items[item.item_code];

				// Storage: transfer STE to_storage > WO wip_storage > Company default
				if (!item.storage && item.s_warehouse) {
					let src_storage = (t_item && t_item.to_storage) || storage.wip_storage;
					if (src_storage) {
						frappe.model.set_value(item.doctype, item.name, 'storage', src_storage);
						updated = true;
					}
				}

				// Batch/serial from transfer STE
				if (t_item) {
					if (!item.batch_no && t_item.batch_no) {
						frappe.model.set_value(item.doctype, item.name, 'batch_no', t_item.batch_no);
						frappe.model.set_value(item.doctype, item.name, 'use_serial_batch_fields', 1);
						updated = true;
					}
					if (!item.serial_no && t_item.serial_nos && t_item.serial_nos.length > 0) {
						frappe.model.set_value(item.doctype, item.name, 'serial_no', t_item.serial_nos.join('\n'));
						frappe.model.set_value(item.doctype, item.name, 'use_serial_batch_fields', 1);
						updated = true;
					}
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

			// Set target storage: WO wip_storage > Company default
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

function set_storage_from_subcontracting_order(frm) {
	if (!frm.doc.subcontracting_order || !frm.doc.items || frm.doc.items.length === 0) {
		return;
	}

	frappe.db.get_value('Subcontracting Order', frm.doc.subcontracting_order, 'supplier_storage')
		.then(r => {
			let supplier_storage = r && r.message && r.message.supplier_storage;
			if (!supplier_storage) return;

			let updated = false;
			frm.doc.items.forEach(function(item) {
				if (!item.to_storage && item.t_warehouse) {
					frappe.model.set_value(item.doctype, item.name, 'to_storage', supplier_storage);
					updated = true;
				}
			});

			if (updated) {
				frm.refresh_field('items');
			}
		});
}
