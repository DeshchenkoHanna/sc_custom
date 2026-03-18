"""
Pick List overrides for SC Custom.

Monkey-patches Pick List to:
1. Respect Work Order source warehouses when selecting batch/serial locations
2. Include storage in location allocation (warehouse → storage → batch)
3. Keep rows with different storage separate (not merged by dedup key)

Priority logic:
1. Search WO source warehouse first
2. Fall back to other warehouses (excluding WIP/FG)
3. For each warehouse+batch, split by storage from SABB/SABE or SLE
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, floor

# References to original functions, set during apply_pick_list_patches()
_original_set_item_locations = None
_original_get_available_item_locations = None


@frappe.whitelist()
def patched_set_item_locations(self, save=False):
    """Full override of set_item_locations that includes storage in dedup key.

    Replicates the standard logic but adds storage to the row grouping key,
    so rows with same item+warehouse+batch but different storage stay separate.
    """
    from erpnext.stock.doctype.pick_list.pick_list import (
        get_available_item_locations,
        get_descendants_of,
        get_items_with_location_and_quantity,
    )

    # Set WO warehouse context
    if self.work_order and not self.parent_warehouse:
        wo_items = frappe.get_all(
            "Work Order Item",
            filters={"parent": self.work_order},
            fields=["item_code", "source_warehouse"],
        )
        wo_item_warehouses = {}
        for wo_item in wo_items:
            if wo_item.source_warehouse:
                wo_item_warehouses.setdefault(wo_item.item_code, [])
                if wo_item.source_warehouse not in wo_item_warehouses[wo_item.item_code]:
                    wo_item_warehouses[wo_item.item_code].append(wo_item.source_warehouse)

        frappe.local._sc_custom_wo_item_warehouses = wo_item_warehouses
        frappe.local._sc_custom_wo_name = self.work_order

    try:
        # --- Standard set_item_locations logic (with storage in dedup key) ---
        self.validate_for_qty()
        items = self.aggregate_item_qty()
        picked_items_details = self.get_picked_items_details(items)
        self.item_location_map = frappe._dict()

        from_warehouses = [self.parent_warehouse] if self.parent_warehouse else []
        if self.parent_warehouse:
            from_warehouses.extend(get_descendants_of("Warehouse", self.parent_warehouse))

        locations_replica = self.get("locations")

        # Reset unpicked rows
        reset_rows = [row for row in self.get("locations") if not row.picked_qty]
        for row in reset_rows:
            self.remove(row)

        updated_locations = frappe._dict()
        len_idx = len(self.get("locations")) or 0

        for item_doc in items:
            item_code = item_doc.item_code

            self.item_location_map.setdefault(
                item_code,
                get_available_item_locations(
                    item_code,
                    from_warehouses,
                    self.item_count_map.get(item_code),
                    self.company,
                    picked_item_details=picked_items_details.get(item_code),
                    consider_rejected_warehouses=self.consider_rejected_warehouses,
                ),
            )

            locations = get_items_with_location_and_quantity(
                item_doc, self.item_location_map, self.docstatus
            )

            item_doc.idx = None
            item_doc.name = None

            for row in locations:
                location = item_doc.as_dict()
                location.update(row)
                # Include storage in dedup key so different storages stay separate
                key = (
                    location.item_code,
                    location.warehouse,
                    location.uom,
                    location.batch_no,
                    location.serial_no,
                    location.get("storage"),
                    location.sales_order_item or location.material_request_item,
                )

                if key not in updated_locations:
                    updated_locations.setdefault(key, location)
                else:
                    updated_locations[key].qty += location.qty
                    updated_locations[key].stock_qty += location.stock_qty

        for location in updated_locations.values():
            if location.picked_qty > location.stock_qty:
                location.picked_qty = location.stock_qty

            len_idx += 1
            location.idx = len_idx
            self.append("locations", location)

        if not self.get("locations") and self.docstatus == 1:
            for location in locations_replica:
                location.stock_qty = 0
                location.picked_qty = 0

                len_idx += 1
                location.idx = len_idx
                self.append("locations", location)

            frappe.msgprint(
                _(
                    "Please Restock Items and Update the Pick List to continue. "
                    "To discontinue, cancel the Pick List."
                ),
                title=_("Out of Stock"),
                indicator="red",
            )

        if save:
            self.save()

    finally:
        for attr in ("_sc_custom_wo_item_warehouses", "_sc_custom_wo_name"):
            if hasattr(frappe.local, attr):
                delattr(frappe.local, attr)


def _get_exclude_warehouses():
    """Get warehouses to exclude from fallback search (WIP + FG)."""
    exclude = set()

    ms = frappe.get_cached_doc("Manufacturing Settings")
    if ms.default_wip_warehouse:
        exclude.add(ms.default_wip_warehouse)
    if ms.default_fg_warehouse:
        exclude.add(ms.default_fg_warehouse)

    wo_name = getattr(frappe.local, "_sc_custom_wo_name", None)
    if wo_name:
        wo_data = frappe.db.get_value(
            "Work Order", wo_name, ["wip_warehouse", "fg_warehouse"], as_dict=True
        )
        if wo_data:
            if wo_data.wip_warehouse:
                exclude.add(wo_data.wip_warehouse)
            if wo_data.fg_warehouse:
                exclude.add(wo_data.fg_warehouse)

    return exclude


def _get_raw_locations(item_code, from_warehouses, required_qty, company, consider_rejected_warehouses):
    """Call the appropriate sub-function based on item type to get raw locations."""
    from erpnext.stock.doctype.pick_list.pick_list import (
        get_available_item_locations_for_batched_item,
        get_available_item_locations_for_other_item,
        get_available_item_locations_for_serial_and_batched_item,
        get_available_item_locations_for_serialized_item,
    )

    has_serial_no = frappe.get_cached_value("Item", item_code, "has_serial_no")
    has_batch_no = frappe.get_cached_value("Item", item_code, "has_batch_no")

    if has_batch_no and has_serial_no:
        return get_available_item_locations_for_serial_and_batched_item(
            item_code, from_warehouses, required_qty, company,
            consider_rejected_warehouses=consider_rejected_warehouses,
        )
    elif has_serial_no:
        return get_available_item_locations_for_serialized_item(
            item_code, from_warehouses, company,
            consider_rejected_warehouses=consider_rejected_warehouses,
        )
    elif has_batch_no:
        return get_available_item_locations_for_batched_item(
            item_code, from_warehouses,
            consider_rejected_warehouses=consider_rejected_warehouses,
        )
    else:
        return get_available_item_locations_for_other_item(
            item_code, from_warehouses, company,
            consider_rejected_warehouses=consider_rejected_warehouses,
        )


def _get_storage_for_batch(item_code, warehouse, batch_no):
    """Get storage breakdown for a specific batch in a warehouse.

    Joins SLE (source of storage) with SABB/SABE (source of batch_no)
    via the serial_and_batch_bundle link.
    """
    return frappe.db.sql("""
        SELECT sle.storage, SUM(sle.actual_qty) as qty
        FROM `tabStock Ledger Entry` sle
        JOIN `tabSerial and Batch Bundle` sabb ON sle.serial_and_batch_bundle = sabb.name
        JOIN `tabSerial and Batch Entry` sabe ON sabe.parent = sabb.name
        WHERE sle.item_code = %(item_code)s
            AND sle.warehouse = %(warehouse)s
            AND sabe.batch_no = %(batch_no)s
            AND sle.is_cancelled = 0
            AND sle.storage IS NOT NULL
            AND sle.storage != ''
        GROUP BY sle.storage
        HAVING SUM(sle.actual_qty) > 0
        ORDER BY MIN(sle.posting_date), MIN(sle.posting_time), MIN(sle.creation)
    """, {"item_code": item_code, "warehouse": warehouse, "batch_no": batch_no}, as_dict=True)


def _get_storage_for_item(item_code, warehouse):
    """Get storage breakdown for a non-batch item in a warehouse from SLE."""
    return frappe.db.sql("""
        SELECT sle.storage, SUM(sle.actual_qty) as qty
        FROM `tabStock Ledger Entry` sle
        WHERE sle.item_code = %(item_code)s
            AND sle.warehouse = %(warehouse)s
            AND sle.is_cancelled = 0
            AND sle.storage IS NOT NULL
            AND sle.storage != ''
        GROUP BY sle.storage
        HAVING SUM(sle.actual_qty) > 0
        ORDER BY MIN(sle.posting_date), MIN(sle.posting_time), MIN(sle.creation)
    """, {"item_code": item_code, "warehouse": warehouse}, as_dict=True)


def _expand_locations_with_storage(locations, item_code=None):
    """Expand location list: split each entry by storage.

    For serialized items (locations with serial_nos list):
        Query each serial number's storage from Serial No master,
        group by storage, create one location per storage group.

    For non-serialized items (batch-only or plain):
        Split qty by storage from SABB/SABE or SLE data.
    """
    expanded = []

    for loc in locations:
        # --- Serialized items: group serial_nos by their actual storage ---
        if loc.get("serial_nos"):
            sn_data = frappe.get_all(
                "Serial No",
                filters={"name": ("in", loc.serial_nos)},
                fields=["name", "storage"],
                order_by="creation",
            )

            if not sn_data:
                expanded.append(loc)
                continue

            storage_groups = {}
            for sn in sn_data:
                storage_groups.setdefault(sn.storage or "", []).append(sn.name)

            for storage, sns in storage_groups.items():
                new_loc = frappe._dict(loc.copy())
                new_loc.serial_nos = sns
                new_loc.qty = len(sns)
                new_loc.storage = storage or None
                expanded.append(new_loc)

            continue

        # --- Non-serialized items: split qty by storage ---
        loc_item_code = loc.get("item_code") or item_code
        warehouse = loc.get("warehouse")
        batch_no = loc.get("batch_no")

        if batch_no:
            storage_data = _get_storage_for_batch(loc_item_code, warehouse, batch_no)
        else:
            storage_data = _get_storage_for_item(loc_item_code, warehouse)

        if not storage_data:
            expanded.append(loc)
            continue

        remaining = loc.get("qty", 0)
        for sd in storage_data:
            if remaining <= 0:
                break
            alloc_qty = min(float(sd.qty), remaining)
            new_loc = frappe._dict(loc.copy())
            new_loc.qty = alloc_qty
            new_loc.storage = sd.storage
            expanded.append(new_loc)
            remaining -= alloc_qty

        # Remainder without storage
        if remaining > 0:
            new_loc = frappe._dict(loc.copy())
            new_loc.qty = remaining
            new_loc.storage = None
            expanded.append(new_loc)

    return expanded


def patched_get_available_item_locations(
    item_code,
    from_warehouses,
    required_qty,
    company,
    ignore_validation=False,
    picked_item_details=None,
    consider_rejected_warehouses=False,
):
    """Override: prioritize WO warehouse, expand with storage, then trim."""
    wo_warehouses = None
    if not from_warehouses and hasattr(frappe.local, "_sc_custom_wo_item_warehouses"):
        wo_warehouses = frappe.local._sc_custom_wo_item_warehouses.get(item_code)

    if not wo_warehouses:
        # No WO context — use original, then add storage
        locations = _original_get_available_item_locations(
            item_code, from_warehouses, required_qty, company,
            ignore_validation=ignore_validation,
            picked_item_details=picked_item_details,
            consider_rejected_warehouses=consider_rejected_warehouses,
        )
        return _expand_locations_with_storage(locations, item_code=item_code)

    from erpnext.stock.doctype.pick_list.pick_list import (
        filter_locations_by_picked_materials,
        get_locations_based_on_required_qty,
        validate_picked_materials,
    )

    exclude = _get_exclude_warehouses()

    locations = _get_raw_locations(
        item_code, [], required_qty, company, consider_rejected_warehouses
    )

    # Filter out WIP/FG warehouses
    locations = [loc for loc in locations if loc.warehouse not in exclude]

    # Filter by already-picked materials
    if picked_item_details:
        locations = filter_locations_by_picked_materials(locations, picked_item_details)

    # Reorder: WO source warehouse first
    wo_set = set(wo_warehouses)
    priority = [loc for loc in locations if loc.warehouse in wo_set]
    others = [loc for loc in locations if loc.warehouse not in wo_set]
    locations = priority + others

    # Expand with storage BEFORE trimming
    locations = _expand_locations_with_storage(locations, item_code=item_code)

    # Trim to required qty
    if locations:
        locations = get_locations_based_on_required_qty(locations, required_qty)

    # Validate
    if not ignore_validation:
        validate_picked_materials(item_code, required_qty, locations, picked_item_details)

    return locations


def patched_get_items_with_location_and_quantity(item_doc, item_location_map, docstatus):
    """Standard logic + passes storage from location to the output row."""
    available_locations = item_location_map.get(item_doc.item_code)
    locations = []

    remaining_stock_qty = item_doc.qty if (docstatus == 1 and item_doc.stock_qty == 0) else item_doc.stock_qty
    precision = frappe.get_precision("Pick List Item", "qty")

    while flt(remaining_stock_qty, precision) > 0 and available_locations:
        item_location = available_locations.pop(0)
        item_location = frappe._dict(item_location)

        stock_qty = remaining_stock_qty if item_location.qty >= remaining_stock_qty else item_location.qty
        qty = stock_qty / (item_doc.conversion_factor or 1)

        uom_must_be_whole_number = frappe.get_cached_value("UOM", item_doc.uom, "must_be_whole_number")
        if uom_must_be_whole_number:
            qty = floor(qty)
            stock_qty = qty * item_doc.conversion_factor
            if not stock_qty:
                break

        serial_nos = None
        if item_location.serial_nos:
            serial_nos = "\n".join(item_location.serial_nos[0 : cint(stock_qty)])

        row = frappe._dict({
            "qty": qty,
            "stock_qty": stock_qty,
            "warehouse": item_location.warehouse,
            "serial_no": serial_nos,
            "batch_no": item_location.batch_no,
            "use_serial_batch_fields": 1,
        })

        if item_location.get("storage"):
            row.storage = item_location.storage

        locations.append(row)

        remaining_stock_qty -= stock_qty

        qty_diff = item_location.qty - stock_qty
        if qty_diff > 0:
            item_location.qty = qty_diff
            if item_location.serial_no:
                item_location.serial_no = item_location.serial_no[-int(qty_diff) :]
            available_locations = [item_location, *available_locations]

    item_location_map[item_doc.item_code] = available_locations
    return locations


def apply_pick_list_patches():
    """Apply monkey-patches for Pick List."""
    global _original_set_item_locations, _original_get_available_item_locations

    from erpnext.stock.doctype.pick_list import pick_list as pick_list_module

    PickList = pick_list_module.PickList

    # Save originals
    _original_set_item_locations = PickList.set_item_locations
    _original_get_available_item_locations = pick_list_module.get_available_item_locations

    # Apply patches
    PickList.set_item_locations = patched_set_item_locations
    pick_list_module.get_available_item_locations = patched_get_available_item_locations
    pick_list_module.get_items_with_location_and_quantity = patched_get_items_with_location_and_quantity
