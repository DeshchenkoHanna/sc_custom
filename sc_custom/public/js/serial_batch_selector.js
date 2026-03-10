/**
 * Global override of erpnext.SerialBatchPackageSelector
 *
 * Adds a Storage Link field to the serial/batch dialog for all doctypes.
 * When storage is selected, batch/serial queries and auto-fetch are
 * filtered by storage. The created SABB gets storage set on it.
 */

const OriginalSerialBatchPackageSelector = erpnext.SerialBatchPackageSelector;

erpnext.SerialBatchPackageSelector = class extends OriginalSerialBatchPackageSelector {

	get_dialog_fields() {
		let fields = super.get_dialog_fields();

		// Insert Storage Link field right after Warehouse
		let wh_idx = fields.findIndex(f => f.fieldname === 'warehouse');
		if (wh_idx !== -1) {
			fields.splice(wh_idx + 1, 0, {
				fieldtype: "Link",
				fieldname: "storage",
				label: __("Storage"),
				options: "Storage",
				default: this.item.storage || '',
				onchange: () => {
					this.item.storage = this.dialog.get_value("storage");
					// Clear entries and re-fetch with new storage filter
					this.dialog.fields_dict.entries.df.data = [];
					this.dialog.fields_dict.entries.grid.refresh();
					this.get_auto_data();
				},
				get_query: () => {
					return {
						query: "sc_custom.api.queries.get_storage",
						filters: {
							item_code: this.item.item_code,
							warehouse: this.dialog.get_value("warehouse"),
						}
					};
				}
			});
		}

		// Patch warehouse onchange to also clear storage
		let wh_field = fields.find(f => f.fieldname === 'warehouse');
		if (wh_field) {
			let original_onchange = wh_field.onchange;
			wh_field.onchange = () => {
				if (this.dialog) {
					this.dialog.set_value("storage", "");
					this.item.storage = "";
				}
				if (original_onchange) original_onchange();
			};
		}

		return fields;
	}

	make() {
		super.make();
		this._patch_entries_table_queries();
	}

	_patch_entries_table_queries() {
		let entries_field = this.dialog?.fields_dict?.entries;
		if (!entries_field) return;

		let table_fields = entries_field.df.fields;
		for (let f of table_fields) {
			if (f.fieldname === 'batch_no') {
				let original_get_query = f.get_query;
				f.get_query = () => {
					let storage = this.dialog?.get_value("storage") || this.item.storage;
					if (storage) {
						return {
							query: "sc_custom.api.queries.get_batch_no",
							filters: {
								item_code: this.item.item_code,
								warehouse: this.dialog?.get_value("warehouse") || this.item.s_warehouse || this.item.t_warehouse || this.item.warehouse,
								storage: storage,
							}
						};
					}
					return original_get_query ? original_get_query() : {};
				};
			}
			if (f.fieldname === 'serial_no') {
				let original_get_query = f.get_query;
				f.get_query = () => {
					let storage = this.dialog?.get_value("storage") || this.item.storage;
					if (storage) {
						return {
							query: "sc_custom.api.queries.get_serial_no",
							filters: {
								item_code: this.item.item_code,
								warehouse: this.dialog?.get_value("warehouse") || this.item.warehouse,
								storage: storage,
							}
						};
					}
					return original_get_query ? original_get_query() : {};
				};
			}
		}
	}

	get_serial_no_filters() {
		let filters = super.get_serial_no_filters();
		let storage = this.dialog?.get_value("storage") || this.item.storage;
		if (storage) {
			filters.storage = ["=", storage];
		}
		return filters;
	}

	get_auto_data() {
		let storage = this.dialog?.get_value("storage") || this.item.storage;

		if (!storage) {
			return super.get_auto_data();
		}

		// Same early-return guards as standard get_auto_data
		let { qty, based_on } = this.dialog.get_values();

		if (this.item.serial_and_batch_bundle || this.item.rejected_serial_and_batch_bundle) {
			if (this.qty && qty === Math.abs(this.qty)) {
				return;
			}
		}

		if (this.item.serial_no || this.item.batch_no) {
			return;
		}

		if (!based_on) based_on = "FIFO";

		let warehouse = this.item.warehouse || this.item.s_warehouse;
		if (this.item?.is_rejected) {
			warehouse = this.item.rejected_warehouse;
		}

		if (qty) {
			let method = this.item.has_serial_no
				? "sc_custom.api.queries.get_auto_serial_nos_with_storage"
				: "sc_custom.api.queries.get_auto_batch_nos_with_storage";

			frappe.call({
				method: method,
				args: {
					item_code: this.item.item_code,
					warehouse: warehouse,
					storage: storage,
					qty: qty,
					based_on: based_on,
				},
				callback: (r) => {
					if (r.message) {
						this.dialog.fields_dict.entries.df.data = r.message;
						this.dialog.fields_dict.entries.grid.refresh();
					}
				},
			});
		}
	}

	update_bundle_entries() {
		let storage = this.dialog?.get_value("storage") || "";

		if (!storage) {
			return super.update_bundle_entries();
		}

		// Same validation as standard
		let entries = this.dialog.get_values().entries;
		let warehouse = this.dialog.get_value("warehouse");
		let upload_serial_nos = this.dialog.get_value("upload_serial_nos");

		if (!entries?.length && upload_serial_nos) {
			this.create_serial_nos();
			return;
		}

		if ((entries && !entries.length) || !entries) {
			frappe.throw(__("Please add atleast one Serial No / Batch No"));
		}

		if (!warehouse) {
			frappe.throw(__("Please select a Warehouse"));
		}

		if (this.item?.is_rejected && this.item.rejected_warehouse === this.item.warehouse) {
			frappe.throw(__("Rejected Warehouse and Accepted Warehouse cannot be same."));
		}

		frappe.call({
			method: "erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle.add_serial_batch_ledgers",
			args: {
				entries: entries,
				child_row: this.item,
				doc: this.frm.doc,
				warehouse: warehouse,
			},
		}).then((r) => {
			frappe.run_serially([
				() => {
					// Set storage on the created SABB before save
					if (r.message && storage) {
						return frappe.call({
							method: "sc_custom.api.queries.set_bundle_storage",
							args: {
								bundle_name: r.message.name,
								storage: storage,
							},
						});
					}
				},
				() => {
					this.item.storage = storage;
					this.callback && this.callback(r.message);
				},
				() => this.frm.save(),
				() => this.dialog.hide(),
			]);
		});
	}
};
