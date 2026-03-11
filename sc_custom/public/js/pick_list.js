/**
 * Pick List customizations for SC Custom
 *
 * - Storage and batch_no field queries filtered by warehouse/storage
 * - Cascade clearing: warehouse change → clear storage + batch,
 *   storage change → clear batch
 * - Pick Serial/Batch dialog patched to filter by storage
 */

frappe.ui.form.on('Pick List', {
	refresh: function(frm) {
		sc_toggle_pick_serial_and_batch(frm);
	},

	pick_manually: function(frm) {
		sc_toggle_pick_serial_and_batch(frm);
	},

	setup: function(frm) {
		// Storage query: filter by item_code + warehouse, show only storages with stock
		frm.set_query("storage", "locations", (_frm, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: "sc_custom.api.queries.get_storage",
				filters: {
					item_code: row.item_code,
					warehouse: row.warehouse,
				},
			};
		});

		// Batch query: filter by item_code + warehouse + storage
		frm.set_query("batch_no", "locations", (_frm, cdt, cdn) => {
			const row = locals[cdt][cdn];
			let filters = {
				item_code: row.item_code,
				warehouse: row.warehouse,
			};
			if (row.storage) {
				filters.storage = row.storage;
			}
			return {
				query: "sc_custom.api.queries.get_batch_no",
				filters: filters,
			};
		});
	}
});

function sc_toggle_pick_serial_and_batch(frm) {
	let grid = frm.fields_dict?.locations?.grid;
	if (!grid) return;
	let field = grid.get_field?.("pick_serial_and_batch");
	if (!field) return;
	field.hidden = frm.doc.pick_manually ? 0 : 1;
	grid.refresh();
}

// Clear standard pick_serial_and_batch handler so only our custom one runs
if (frappe.ui.form.handlers['Pick List Item']) {
	frappe.ui.form.handlers['Pick List Item']['pick_serial_and_batch'] = [];
}

// Pick List Item child table events
frappe.ui.form.on('Pick List Item', {
	warehouse: function(frm, cdt, cdn) {
		// When warehouse changes, clear storage and batch_no
		// Skip during dialog callback sync to avoid overwriting dialog values
		if (frm._sc_skip_cascade) return;
		frappe.model.set_value(cdt, cdn, 'storage', '');
		frappe.model.set_value(cdt, cdn, 'batch_no', '');
	},

	storage: function(frm, cdt, cdn) {
		// When storage changes, clear batch_no
		if (frm._sc_skip_cascade) return;
		frappe.model.set_value(cdt, cdn, 'batch_no', '');
	},

	pick_serial_and_batch: function(frm, cdt, cdn) {
		if (!frm.doc.pick_manually) {
			frappe.msgprint(__("Enable 'Pick Manually' on the Pick List to pick serial/batch numbers manually."));
			return;
		}

		// Override standard handler to pass storage context to the dialog
		let item = locals[cdt][cdn];
		frappe.db.get_value("Item", item.item_code, ["has_batch_no", "has_serial_no"]).then((r) => {
			if (r.message && (r.message.has_batch_no || r.message.has_serial_no)) {
				item.has_serial_no = r.message.has_serial_no;
				item.has_batch_no = r.message.has_batch_no;
				item.type_of_transaction = item.qty > 0 ? "Outward" : "Inward";

				item.title = item.has_serial_no ? __("Select Serial No") : __("Select Batch No");
				if (item.has_serial_no && item.has_batch_no) {
					item.title = __("Select Serial and Batch");
				}

				// Create dialog with storage-aware patches
				let selector = new erpnext.SerialBatchPackageSelector(frm, item, (r) => {
					if (r) {
						let qty = Math.abs(r.total_qty);
						let dialog_warehouse = selector.dialog.get_value("warehouse");
						let dialog_storage = item.storage || '';

						// Set values synchronously on the row object.
						// Standard update_bundle_entries discards callback's return value,
						// so async frappe.model.set_value won't complete before frm.save().
						item.serial_and_batch_bundle = r.name;
						item.use_serial_batch_fields = 0;
						item.serial_no = '';
						item.batch_no = '';
						item.qty = qty / flt(item.conversion_factor || 1,
							precision("conversion_factor", item));
						item.stock_qty = item.qty * (item.conversion_factor || 1);
						item.warehouse = dialog_warehouse;
						item.storage = dialog_storage;
						frm.dirty();

						// Set storage on the SABB and its entries
						if (dialog_storage) {
							frappe.call({
								method: "sc_custom.api.queries.set_bundle_storage",
								args: {
									bundle_name: r.name,
									storage: dialog_storage,
								},
							});
						}
					}
				});

				if (!selector.dialog) return;

				// Old Autocomplete storage field — replaced by Link field in
				// global SerialBatchPackageSelector override (serial_batch_selector.js).
				// Kept commented for reference.
				//
				// let wh_control_el = selector.dialog.fields_dict.warehouse.$wrapper.closest('.frappe-control');
				// wh_control_el.css({
				// 	'width': 'calc(50% - 8px)',
				// 	'display': 'inline-block',
				// 	'vertical-align': 'top',
				// 	'margin-right': '16px'
				// });
				// let $storage_container = $('<div class="frappe-control" style="width: calc(50% - 8px); display: inline-block; vertical-align: top;"></div>');
				// $storage_container.insertAfter(wh_control_el);
				// $('<style>.modal .awesomplete > ul { z-index: 1060 !important; }</style>')
				// 	.appendTo(selector.dialog.$wrapper);
				// let storage_control = frappe.ui.form.make_control({
				// 	df: {
				// 		fieldtype: "Autocomplete",
				// 		fieldname: "storage",
				// 		label: __("Storage"),
				// 		placeholder: __("Select Storage"),
				// 		ignore_validation: 1,
				// 		get_query: function() {
				// 			let warehouse = selector.dialog.get_value("warehouse");
				// 			return {
				// 				query: "sc_custom.api.queries.get_storage_for_autocomplete",
				// 				params: {
				// 					item_code: item.item_code,
				// 					warehouse: warehouse || "",
				// 				}
				// 			};
				// 		},
				// 	},
				// 	parent: $storage_container,
				// 	render_input: true,
				// });
				// storage_control.set_value(item.storage || '');
				// storage_control.$input.on('focus', function() {
				// 	storage_control.$input.trigger('input');
				// });
				// storage_control.$input.on('change', function() {
				// 	let new_storage = storage_control.get_value();
				// 	if (new_storage !== item.storage) {
				// 		item.storage = new_storage;
				// 		selector.dialog.fields_dict.entries.df.data = [];
				// 		selector.dialog.fields_dict.entries.grid.refresh();
				// 		selector.get_auto_data();
				// 	}
				// });
				// selector._storage_control = storage_control;
				// let wh_field = selector.dialog.fields_dict.warehouse;
				// let original_wh_change = wh_field.df.onchange;
				// wh_field.df.onchange = function() {
				// 	storage_control.set_value('');
				// 	item.storage = '';
				// 	if (original_wh_change) original_wh_change();
				// };

				// Entries table queries, auto-fetch, and scan_serial_no patches
				// are now handled by global SerialBatchPackageSelector override.
				// Kept commented for reference.
				//
				// // Patch entries table queries to include storage
				// let entries_field = selector.dialog.fields_dict.entries;
				// if (entries_field) {
				// 	let table_fields = entries_field.df.fields;
				// 	for (let f of table_fields) {
				// 		if (f.fieldname === 'batch_no' && item.has_batch_no) {
				// 			f.get_query = () => {
				// 				return {
				// 					query: "sc_custom.api.queries.get_batch_no",
				// 					filters: {
				// 						item_code: item.item_code,
				// 						warehouse: selector.dialog.get_value("warehouse") || item.warehouse,
				// 						storage: item.storage || '',
				// 					},
				// 				};
				// 			};
				// 		}
				// 		if (f.fieldname === 'serial_no' && item.has_serial_no) {
				// 			f.get_query = () => {
				// 				return {
				// 					query: "sc_custom.api.queries.get_serial_no",
				// 					filters: {
				// 						item_code: item.item_code,
				// 						warehouse: selector.dialog.get_value("warehouse") || item.warehouse,
				// 						storage: item.storage || '',
				// 					},
				// 				};
				// 			};
				// 		}
				// 	}
				// }
				//
				// // Patch scan_serial_no field query to include storage
				// if (item.has_serial_no && selector.dialog.fields_dict.scan_serial_no) {
				// 	selector.dialog.fields_dict.scan_serial_no.df.get_query = () => {
				// 		return {
				// 			query: "sc_custom.api.queries.get_serial_no",
				// 			filters: {
				// 				item_code: item.item_code,
				// 				warehouse: selector.dialog.get_value("warehouse") || item.warehouse,
				// 				storage: item.storage || '',
				// 			},
				// 		};
				// 	};
				// }
				//
				// // Patch auto-fetch to use storage-filtered method
				// let original_get_auto_data = selector.get_auto_data.bind(selector);
				// selector.get_auto_data = function() {
				// 	let values = this.dialog.get_values();
				// 	let qty = values.qty;
				// 	if (item.serial_and_batch_bundle) {
				// 		let existing_qty = Math.abs(item.stock_qty || item.transfer_qty || item.qty || 0);
				// 		if (qty === existing_qty) return;
				// 	}
				// 	if (item.serial_no || item.batch_no) return;
				// 	let based_on = values.based_on || "FIFO";
				// 	let warehouse = this.dialog.get_value("warehouse") || item.warehouse;
				// 	if (qty && item.storage) {
				// 		let method = item.has_serial_no
				// 			? "sc_custom.api.queries.get_auto_serial_nos_with_storage"
				// 			: "sc_custom.api.queries.get_auto_batch_nos_with_storage";
				// 		frappe.call({
				// 			method: method,
				// 			args: {
				// 				item_code: item.item_code,
				// 				warehouse: warehouse,
				// 				storage: item.storage,
				// 				qty: qty,
				// 				based_on: based_on,
				// 			},
				// 			callback: (r) => {
				// 				if (r.message) {
				// 					this.dialog.fields_dict.entries.df.data = r.message;
				// 					this.dialog.fields_dict.entries.grid.refresh();
				// 				}
				// 			},
				// 		});
				// 	} else {
				// 		original_get_auto_data();
				// 	}
				// };
				//
				// if (item.storage) {
				// 	selector.get_auto_data();
				// }
			}
		});
	}
});
