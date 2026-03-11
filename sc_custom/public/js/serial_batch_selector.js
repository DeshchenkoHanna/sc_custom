/**
 * Global override of erpnext.SerialBatchPackageSelector
 *
 * Adds a Storage Link field to the serial/batch dialog for all doctypes.
 * When storage is selected, batch/serial queries and auto-fetch are
 * filtered by storage. The created SABB gets storage set on it.
 *
 * Handles both Outward (source storage) and Inward (target to_storage)
 * directions via type_of_transaction on the item.
 */

const OriginalSerialBatchPackageSelector = erpnext.SerialBatchPackageSelector;

erpnext.SerialBatchPackageSelector = class extends OriginalSerialBatchPackageSelector {

	/**
	 * Detect if this dialog is for an inward (target) transaction.
	 * Uses the same type_of_transaction that standard ERPNext sets.
	 */
	_is_inward() {
		return this.item?.type_of_transaction !== "Outward";
	}

	/**
	 * Get the current storage value from the item row,
	 * respecting direction (storage for outward, to_storage for inward).
	 */
	_get_item_storage() {
		if (this._is_inward()) {
			return this.item.to_storage || this.item.storage || '';
		}
		return this.item.storage || '';
	}

	/**
	 * Set storage back on the item row after dialog confirms,
	 * writing to the correct field based on direction.
	 */
	_set_item_storage(value) {
		if (this._is_inward()) {
			this.item.to_storage = value;
		} else {
			this.item.storage = value;
		}
	}

	/**
	 * Return the warehouse for this item from the item row (not dialog).
	 * Used to detect whether a warehouse onchange is a genuine user change
	 * or just the initial blur-triggered model-value synchronisation.
	 */
	_get_item_warehouse() {
		if (this._is_inward()) {
			return this.item.t_warehouse || this.item.warehouse || '';
		}
		return this.item.s_warehouse || this.item.warehouse || '';
	}

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
				default: this._get_item_storage(),
				onchange: () => {
					// Mirror standard warehouse pattern: store value on item row immediately.
					this._set_item_storage(this.dialog.get_value("storage"));
					// Clear entries on storage change.
					// For outward: re-fetch existing serial/batch from new storage.
					// For inward (Material Receipt, finished goods): no auto-fetch —
					// entries are entered manually for newly received/produced items.
					this.dialog.fields_dict.entries.df.data = [];
					this.dialog.fields_dict.entries.grid.refresh();
					if (!this._is_inward()) {
						this.get_auto_data();
					}
				},
				get_query: () => {
					if (this._is_inward()) {
						// For inward: validate_link uses get_query filters as plain DB filters.
						// item_code/warehouse are not Storage fields → validation fails → field clears.
						// Use only is_group=0 (a real Storage field) so validate_link passes.
						return { filters: { is_group: 0 } };
					}
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

		// Patch warehouse onchange to clear storage when warehouse changes.
		// Only clear if the new warehouse differs from the item row's warehouse —
		// this prevents the initial blur-triggered model-value sync (which fires
		// onchange even when the value hasn't actually changed) from wiping storage.
		let wh_field = fields.find(f => f.fieldname === 'warehouse');
		if (wh_field) {
			let original_onchange = wh_field.onchange;
			wh_field.onchange = () => {
				let new_wh = this.dialog?.get_value("warehouse");
				if (this.dialog && new_wh && new_wh !== this._get_item_warehouse()) {
					this.dialog.set_value("storage", "");
				}
				if (original_onchange) original_onchange();
			};
		}

		return fields;
	}

	make() {
		super.make();
		this._patch_entries_table_queries();

		// Explicitly set the storage model value so dialog.get_value("storage")
		// returns it even if the user never interacts with the field.
		// For inward: get_query returns { filters: { is_group: 0 } } which is a valid
		// Storage field — so validate_link passes for any leaf storage record.
		// NOTE: we do NOT call set_value("warehouse") here — doing so triggers an async
		// validation that fires warehouse onchange AFTER our storage is set, clearing it.
		// Warehouse is already handled correctly by the parent make().
		let initial_storage = this._get_item_storage();
		if (initial_storage) {
			this.dialog.set_value("storage", initial_storage);
		}
	}

	_patch_entries_table_queries() {
		let entries_field = this.dialog?.fields_dict?.entries;
		if (!entries_field) return;

		let table_fields = entries_field.df.fields;
		for (let f of table_fields) {
			if (f.fieldname === 'batch_no') {
				let original_get_query = f.get_query;
				f.get_query = () => {
					let storage = this.dialog?.get_value("storage") || this._get_item_storage();
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
					let storage = this.dialog?.get_value("storage") || this._get_item_storage();
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
		let storage = this.dialog?.get_value("storage") || this._get_item_storage();
		if (storage) {
			filters.storage = ["=", storage];
		}
		return filters;
	}

	get_auto_data() {
		// For inward transactions (Material Receipt, finished goods), serial/batch
		// entries are created new — fetching existing stock makes no sense and causes
		// wrong data to appear (empty warehouse query returns items from all warehouses).
		if (this._is_inward()) {
			return;
		}

		let storage = this.dialog?.get_value("storage") || this._get_item_storage();

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
		let storage = this._get_item_storage() || this.dialog?.get_value("storage") || "";

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

		// Phase 9 monkey-patch (patched_create_serial_batch_no_ledgers) reads storage
		// from child_row.storage. For outward this.item.storage is already correct.
		// For inward the value lives in this.item.to_storage, so we normalise here.
		this.item.storage = storage;

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
					// Write storage to the correct item field based on direction.
					// _set_item_storage restores to_storage for inward / storage for outward.
					this._set_item_storage(storage);
					// For inward: we temporarily set item.storage above for Phase 9.
					// Clear it now so only to_storage is set on the row — storage is
					// the source field and must be empty for receiving transactions.
					if (this._is_inward()) {
						this.item.storage = '';
					}
					this.callback && this.callback(r.message);
				},
				() => this.frm.save(),
				() => this.dialog.hide(),
			]);
		});
	}
};
