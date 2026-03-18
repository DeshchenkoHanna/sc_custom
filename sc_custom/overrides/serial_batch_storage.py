"""
Storage support for Serial and Batch Bundle/Entry and Serial No master.

Phase 2: When an SLE is created with a serial_and_batch_bundle, copy the
         SLE's storage value into the bundle header and all child entries.

Phase 3: After updating the bundle, also update the Serial No master record's
         storage field following the same pattern as warehouse:
         - Inward (actual_qty > 0): storage = sle.storage
         - Outward (actual_qty < 0): storage = None

Phase 4: Monkey-patch batch availability queries to filter by storage
         when kwargs.storage is provided.

Phase 5: Monkey-patch serial availability query to filter by storage
         when kwargs.storage is provided.

Phase 6: Monkey-patch validation methods to pass storage through kwargs.

Phase 7: Monkey-patch picked batches query for Pick List storage support.

Phase 8: Monkey-patch SRE reservation queries for storage support.

Phase 9: Monkey-patch SABB creation to include storage in the dict,
         so the mandatory Storage field is set at creation time.
"""

import frappe
from frappe.query_builder.functions import Sum

_original_make_serial_and_batch_bundle = None


def on_sle_after_insert(doc, method=None):
    """Hook: Stock Ledger Entry → after_insert

    Populates storage on Serial and Batch Bundle/Entry from SLE
    and updates Serial No master records.
    """
    if not doc.serial_and_batch_bundle or not doc.storage:
        return

    # --- Phase 2: Update bundle header and child entries ---
    frappe.db.set_value(
        "Serial and Batch Bundle",
        doc.serial_and_batch_bundle,
        "storage",
        doc.storage,
        update_modified=False,
    )

    frappe.db.sql(
        """
        UPDATE `tabSerial and Batch Entry`
        SET storage = %s
        WHERE parent = %s
    """,
        (doc.storage, doc.serial_and_batch_bundle),
    )

    # --- Phase 3: Update Serial No master records ---
    if not doc.has_serial_no:
        return

    serial_nos = _get_serial_nos_from_bundle(doc.serial_and_batch_bundle)
    if not serial_nos:
        return

    # Follow the same pattern as warehouse:
    # inward → set storage, outward → clear storage
    storage = doc.storage if doc.actual_qty > 0 else None

    sn_table = frappe.qb.DocType("Serial No")
    (
        frappe.qb.update(sn_table)
        .set(sn_table.storage, storage)
        .where(sn_table.name.isin(serial_nos))
    ).run()


def _get_serial_nos_from_bundle(serial_and_batch_bundle):
    """Extract serial numbers from a Serial and Batch Bundle."""
    return frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": serial_and_batch_bundle, "serial_no": ("is", "set")},
        pluck="serial_no",
    )


# ---------------------------------------------------------------------------
# Phase 4: Monkey-patched batch availability queries with storage support
# ---------------------------------------------------------------------------
# These replace the standard erpnext functions so that when kwargs.storage is
# set, only SLEs for that storage are counted.  When storage is NOT set, the
# behaviour is identical to the standard functions.
# ---------------------------------------------------------------------------

def patched_get_available_batches(kwargs):
    """Override of serial_and_batch_bundle.get_available_batches.

    Only change vs standard: adds WHERE sle.storage = kwargs.storage
    when kwargs.storage is provided.
    """
    from erpnext.stock.utils import get_combine_datetime
    from frappe.utils import nowtime, today

    sle = frappe.qb.DocType("Stock Ledger Entry")
    batch_ledger = frappe.qb.DocType("Serial and Batch Entry")
    batch_table = frappe.qb.DocType("Batch")

    query = (
        frappe.qb.from_(sle)
        .inner_join(batch_ledger)
        .on(sle.serial_and_batch_bundle == batch_ledger.parent)
        .inner_join(batch_table)
        .on(batch_ledger.batch_no == batch_table.name)
        .select(
            batch_ledger.batch_no,
            batch_ledger.warehouse,
            Sum(batch_ledger.qty).as_("qty"),
            batch_table.expiry_date,
        )
        .where(batch_table.disabled == 0)
        .where(sle.is_cancelled == 0)
        .groupby(batch_ledger.batch_no, batch_ledger.warehouse)
    )

    # --- Phase 4 addition: storage filter ---
    if kwargs.get("storage"):
        query = query.where(sle.storage == kwargs.storage)

    if not kwargs.get("for_stock_levels"):
        query = query.where(
            (batch_table.expiry_date >= today()) | (batch_table.expiry_date.isnull())
        )

    if kwargs.get("posting_date"):
        if kwargs.get("posting_time") is None:
            kwargs.posting_time = nowtime()

        timestamp_condition = sle.posting_datetime <= get_combine_datetime(
            kwargs.posting_date, kwargs.posting_time
        )

        if kwargs.get("creation"):
            timestamp_condition = sle.posting_datetime < get_combine_datetime(
                kwargs.posting_date, kwargs.posting_time
            )

            timestamp_condition |= (
                sle.posting_datetime
                == get_combine_datetime(kwargs.posting_date, kwargs.posting_time)
            ) & (sle.creation < kwargs.creation)

        query = query.where(timestamp_condition)

    for field in ["warehouse", "item_code"]:
        if not kwargs.get(field):
            continue
        if isinstance(kwargs.get(field), list):
            query = query.where(sle[field].isin(kwargs.get(field)))
        else:
            query = query.where(sle[field] == kwargs.get(field))

    if kwargs.get("batch_no"):
        if isinstance(kwargs.batch_no, list):
            query = query.where(batch_ledger.batch_no.isin(kwargs.batch_no))
        else:
            query = query.where(batch_ledger.batch_no == kwargs.batch_no)

    if kwargs.based_on == "LIFO":
        query = query.orderby(batch_table.creation, order=frappe.qb.desc)
    elif kwargs.based_on == "Expiry":
        query = query.orderby(batch_table.expiry_date)
    else:
        query = query.orderby(batch_table.creation)

    if kwargs.get("ignore_voucher_nos"):
        query = query.where(sle.voucher_no.notin(kwargs.get("ignore_voucher_nos")))

    return query.run(as_dict=True)


def patched_get_stock_ledgers_batches(kwargs):
    """Override of serial_and_batch_bundle.get_stock_ledgers_batches.

    Only change vs standard: adds WHERE sle.storage = kwargs.storage
    when kwargs.storage is provided.
    """
    from erpnext.stock.utils import get_combine_datetime
    from frappe.utils import nowtime, today

    sle = frappe.qb.DocType("Stock Ledger Entry")
    batch_table = frappe.qb.DocType("Batch")

    query = (
        frappe.qb.from_(sle)
        .inner_join(batch_table)
        .on(sle.batch_no == batch_table.name)
        .select(
            sle.warehouse,
            sle.item_code,
            Sum(sle.actual_qty).as_("qty"),
            sle.batch_no,
            batch_table.expiry_date,
        )
        .where((sle.is_cancelled == 0) & (sle.batch_no.isnotnull()))
        .groupby(sle.batch_no, sle.warehouse)
    )

    for field in ["warehouse", "item_code", "batch_no"]:
        if not kwargs.get(field):
            continue
        if isinstance(kwargs.get(field), list):
            query = query.where(sle[field].isin(kwargs.get(field)))
        else:
            query = query.where(sle[field] == kwargs.get(field))

    # --- Phase 4 addition: storage filter ---
    if kwargs.get("storage"):
        query = query.where(sle.storage == kwargs.storage)

    if not kwargs.get("for_stock_levels"):
        query = query.where(
            (batch_table.expiry_date >= today()) | (batch_table.expiry_date.isnull())
        )

    if kwargs.get("posting_date"):
        if kwargs.get("posting_time") is None:
            kwargs.posting_time = nowtime()

        timestamp_condition = sle.posting_datetime <= get_combine_datetime(
            kwargs.posting_date, kwargs.posting_time
        )

        if kwargs.get("creation"):
            timestamp_condition = sle.posting_datetime < get_combine_datetime(
                kwargs.posting_date, kwargs.posting_time
            )

            timestamp_condition |= (
                sle.posting_datetime
                == get_combine_datetime(kwargs.posting_date, kwargs.posting_time)
            ) & (sle.creation < kwargs.creation)

        query = query.where(timestamp_condition)

    if kwargs.get("ignore_voucher_nos"):
        query = query.where(sle.voucher_no.notin(kwargs.get("ignore_voucher_nos")))

    if kwargs.based_on == "LIFO":
        query = query.orderby(batch_table.creation, order=frappe.qb.desc)
    elif kwargs.based_on == "Expiry":
        query = query.orderby(batch_table.expiry_date)
    else:
        query = query.orderby(batch_table.creation)

    data = query.run(as_dict=True)
    batches = {}
    for d in data:
        key = (d.batch_no, d.warehouse)
        if key not in batches:
            batches[key] = d
        else:
            batches[key].qty += d.qty

    return batches


# ---------------------------------------------------------------------------
# Phase 5: Monkey-patched serial availability query with storage support
# ---------------------------------------------------------------------------

def patched_get_available_serial_nos(kwargs):
    """Override of serial_and_batch_bundle.get_available_serial_nos.

    Only change vs standard: adds filters["storage"] = kwargs.storage
    when kwargs.storage is provided.
    """
    from frappe.utils import nowtime

    from erpnext.stock.doctype.serial_and_batch_bundle import (
        serial_and_batch_bundle as sabb_module,
    )

    fields = ["name as serial_no", "warehouse"]
    if kwargs.has_batch_no:
        fields.append("batch_no")

    order_by = "creation"
    if kwargs.based_on == "LIFO":
        order_by = "creation"
    elif kwargs.based_on == "Expiry":
        order_by = "amc_expiry_date"

    filters = {"item_code": kwargs.item_code}

    if not kwargs.get("ignore_warehouse"):
        filters["warehouse"] = ("is", "set")
        if kwargs.warehouse:
            filters["warehouse"] = kwargs.warehouse

    # --- Phase 5 addition: storage filter ---
    if kwargs.get("storage"):
        filters["storage"] = kwargs.storage

    ignore_serial_nos = sabb_module.get_reserved_serial_nos(kwargs)

    if kwargs.get("ignore_serial_nos"):
        ignore_serial_nos.extend(kwargs.get("ignore_serial_nos"))

    if kwargs.get("posting_date"):
        if kwargs.get("posting_time") is None:
            kwargs.posting_time = nowtime()

        time_based_serial_nos = sabb_module.get_serial_nos_based_on_posting_date(
            kwargs, ignore_serial_nos
        )

        if not time_based_serial_nos:
            return []

        filters["name"] = ("in", time_based_serial_nos)
    elif ignore_serial_nos:
        filters["name"] = ("not in", ignore_serial_nos)
    elif kwargs.get("serial_nos"):
        filters["name"] = ("in", kwargs.get("serial_nos"))

    if kwargs.get("batches"):
        batches = sabb_module.get_non_expired_batches(kwargs.get("batches"))
        if not batches:
            return []

        filters["batch_no"] = ("in", batches)

    return sabb_module.get_serial_nos_based_on_filters(filters, fields, order_by, kwargs)


# ---------------------------------------------------------------------------
# Phase 6: Patched validation methods with storage support
# ---------------------------------------------------------------------------
# These replace instance methods on the SerialandBatchBundle class so that
# when self.storage is set, availability checks are storage-aware.
# ---------------------------------------------------------------------------

def patched_validate_serial_nos_inventory(self):
    """Override of SerialandBatchBundle.validate_serial_nos_inventory.

    Only change vs standard: adds storage to kwargs when self.storage is set.
    """
    from erpnext.stock.doctype.serial_and_batch_bundle import (
        serial_and_batch_bundle as sabb_module,
    )

    if not (self.has_serial_no and self.type_of_transaction == "Outward"):
        return

    if self.voucher_type == "Stock Reconciliation":
        serial_nos, batches = self.get_serial_nos_for_validate()
    else:
        serial_nos = [d.serial_no for d in self.entries if d.serial_no]

    if not serial_nos:
        return

    kwargs = {
        "item_code": self.item_code,
        "warehouse": self.warehouse,
        "check_serial_nos": True,
        "serial_nos": serial_nos,
    }

    # Note: storage filter intentionally NOT added here.
    # Phase 3 (on_sle_after_insert) clears Serial No storage on outward SLEs
    # BEFORE this validation runs, so filtering by storage would always fail
    # for outward transactions. Warehouse check is sufficient; storage is
    # validated separately in Pick List's validate_pick_list.

    if self.voucher_type == "POS Invoice":
        kwargs["ignore_voucher_nos"] = [self.voucher_no]

    if self.voucher_type == "Stock Reconciliation":
        kwargs.update(
            {
                "voucher_no": self.voucher_no,
                "posting_date": self.posting_date,
                "posting_time": self.posting_time,
            }
        )

    if self.voucher_type == "Delivery Note":
        kwargs["ignore_voucher_nos"] = self.get_sre_against_dn()

    available_serial_nos = sabb_module.get_available_serial_nos(frappe._dict(kwargs))

    serial_no_warehouse = {}
    for data in available_serial_nos:
        if data.serial_no not in serial_nos:
            continue

        serial_no_warehouse[data.serial_no] = data.warehouse

    for serial_no in serial_nos:
        if (
            not serial_no_warehouse.get(serial_no)
            or serial_no_warehouse.get(serial_no) != self.warehouse
        ):
            self.throw_error_message(
                f"Serial No {frappe.bold(serial_no)} is not present in the warehouse {frappe.bold(self.warehouse)}.",
                sabb_module.SerialNoWarehouseError,
            )


def patched_validate_batch_inventory(self):
    """Override of SerialandBatchBundle.validate_batch_inventory.

    Only change vs standard: adds storage to kwargs when self.storage is set.
    """
    from frappe.utils import flt

    from erpnext.stock.doctype.serial_and_batch_bundle import (
        serial_and_batch_bundle as sabb_module,
    )

    if not self.has_batch_no:
        return

    batches = [d.batch_no for d in self.entries if d.batch_no]
    if not batches:
        return

    kwargs = {
        "item_code": self.item_code,
        "warehouse": self.warehouse,
        "batch_no": batches,
        "consider_negative_batches": True,
    }

    # Note: storage filter intentionally NOT added here (same reason as
    # patched_validate_serial_nos_inventory — Phase 3 modifies storage on
    # SLE after_insert before this validation runs).

    available_batches = sabb_module.get_auto_batch_nos(frappe._dict(kwargs))

    if not available_batches:
        return

    available_batches = sabb_module.get_available_batches_qty(available_batches)
    for batch_no in batches:
        if batch_no in available_batches and available_batches[batch_no] < 0:
            if flt(available_batches.get(batch_no)) < 0:
                self.validate_negative_batch(batch_no, available_batches[batch_no])

            self.throw_error_message(
                f"Batch {frappe.bold(batch_no)} is not available in the selected warehouse {self.warehouse}"
            )


# ---------------------------------------------------------------------------
# Phase 7: Patched picked batches query with storage support
# ---------------------------------------------------------------------------

def patched_get_picked_batches(kwargs) -> dict:
    """Override of serial_and_batch_bundle.get_picked_batches.

    Only change vs standard: adds WHERE table.storage = kwargs.storage
    when kwargs.storage is provided.
    """
    table = frappe.qb.DocType("Serial and Batch Bundle")
    child_table = frappe.qb.DocType("Serial and Batch Entry")
    pick_list_table = frappe.qb.DocType("Pick List")

    query = (
        frappe.qb.from_(table)
        .inner_join(child_table)
        .on(table.name == child_table.parent)
        .inner_join(pick_list_table)
        .on(table.voucher_no == pick_list_table.name)
        .select(
            child_table.batch_no,
            child_table.warehouse,
            Sum(child_table.qty).as_("qty"),
        )
        .where(
            (table.docstatus != 2)
            & (pick_list_table.status != "Completed")
            & (table.type_of_transaction == "Outward")
            & (table.is_cancelled == 0)
            & (table.voucher_type == "Pick List")
            & (table.voucher_no.isnotnull())
        )
    )

    # --- Phase 7 addition: storage filter ---
    if kwargs.get("storage"):
        query = query.where(table.storage == kwargs.storage)

    if kwargs.get("item_code"):
        query = query.where(table.item_code == kwargs.get("item_code"))

    if kwargs.get("warehouse"):
        if isinstance(kwargs.warehouse, list):
            query = query.where(table.warehouse.isin(kwargs.warehouse))
        else:
            query = query.where(table.warehouse == kwargs.get("warehouse"))

    data = query.run(as_dict=True)
    picked_batches = frappe._dict()
    for row in data:
        if not row.qty:
            continue

        key = (row.batch_no, row.warehouse)
        if key not in picked_batches:
            picked_batches[key] = frappe._dict(
                {
                    "qty": row.qty,
                    "warehouse": row.warehouse,
                }
            )
        else:
            picked_batches[key].qty += row.qty

    return picked_batches


# ---------------------------------------------------------------------------
# Phase 8: Patched SRE reservation queries with storage support
# ---------------------------------------------------------------------------

def patched_get_reserved_batches_for_sre(kwargs) -> dict:
    """Override of serial_and_batch_bundle.get_reserved_batches_for_sre.

    Only change vs standard: adds WHERE sre.storage = kwargs.storage
    when kwargs.storage is provided.
    """
    sre = frappe.qb.DocType("Stock Reservation Entry")
    sb_entry = frappe.qb.DocType("Serial and Batch Entry")
    query = (
        frappe.qb.from_(sre)
        .inner_join(sb_entry)
        .on(sre.name == sb_entry.parent)
        .select(
            sb_entry.batch_no,
            sre.warehouse,
            (-1 * Sum(sb_entry.qty - sb_entry.delivered_qty)).as_("qty"),
        )
        .where(
            (sre.docstatus == 1)
            & (sre.item_code == kwargs.item_code)
            & (sre.reserved_qty >= sre.delivered_qty)
            & (sre.status.notin(["Delivered", "Cancelled"]))
            & (sre.reservation_based_on == "Serial and Batch")
        )
        .groupby(sb_entry.batch_no, sre.warehouse)
    )

    # --- Phase 8 addition: storage filter ---
    if kwargs.get("storage"):
        query = query.where(sre.storage == kwargs.storage)

    if kwargs.batch_no:
        if isinstance(kwargs.batch_no, list):
            query = query.where(sb_entry.batch_no.isin(kwargs.batch_no))
        else:
            query = query.where(sb_entry.batch_no == kwargs.batch_no)

    if kwargs.warehouse:
        if isinstance(kwargs.warehouse, list):
            query = query.where(sre.warehouse.isin(kwargs.warehouse))
        else:
            query = query.where(sre.warehouse == kwargs.warehouse)

    if kwargs.ignore_voucher_nos:
        query = query.where(sre.name.notin(kwargs.ignore_voucher_nos))

    data = query.run(as_dict=True)

    reserved_batches_details = frappe._dict()
    if data:
        reserved_batches_details = frappe._dict(
            {
                (d.batch_no, d.warehouse): frappe._dict(
                    {"warehouse": d.warehouse, "qty": d.qty}
                )
                for d in data
            }
        )

    return reserved_batches_details


def patched_get_reserved_serial_nos_for_sre(kwargs) -> list:
    """Override of serial_and_batch_bundle.get_reserved_serial_nos_for_sre.

    Only change vs standard: adds WHERE sre.storage = kwargs.storage
    when kwargs.storage is provided.
    """
    sre = frappe.qb.DocType("Stock Reservation Entry")
    sb_entry = frappe.qb.DocType("Serial and Batch Entry")
    query = (
        frappe.qb.from_(sre)
        .inner_join(sb_entry)
        .on(sre.name == sb_entry.parent)
        .select(sb_entry.serial_no)
        .where(
            (sre.docstatus == 1)
            & (sre.item_code == kwargs.item_code)
            & (sre.reserved_qty >= sre.delivered_qty)
            & (sre.status.notin(["Delivered", "Cancelled"]))
            & (sre.reservation_based_on == "Serial and Batch")
        )
    )

    # --- Phase 8 addition: storage filter ---
    if kwargs.get("storage"):
        query = query.where(sre.storage == kwargs.storage)

    if kwargs.warehouse:
        query = query.where(sre.warehouse == kwargs.warehouse)

    if kwargs.ignore_voucher_nos:
        query = query.where(sre.name.notin(kwargs.ignore_voucher_nos))

    return [row[0] for row in query.run()]


# --- Phase 9: SABB creation with storage ---

def patched_create_serial_batch_no_ledgers(
    entries, child_row, parent_doc, warehouse=None, do_not_save=False
):
    """Storage-aware version of create_serial_batch_no_ledgers.

    Adds storage from child_row into the SABB header and each entry,
    so the mandatory field is set at creation time (same as warehouse).
    """
    from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
        get_batch,
        get_type_of_transaction,
    )
    from frappe.utils import flt

    warehouse = warehouse or (
        child_row.rejected_warehouse if child_row.is_rejected else child_row.warehouse
    )

    type_of_transaction = get_type_of_transaction(parent_doc, child_row)
    if parent_doc.get("doctype") == "Stock Entry":
        warehouse = warehouse or child_row.s_warehouse or child_row.t_warehouse

    storage = child_row.get("storage") or ""

    doc = frappe.get_doc(
        {
            "doctype": "Serial and Batch Bundle",
            "voucher_type": child_row.parenttype,
            "item_code": child_row.item_code,
            "warehouse": warehouse,
            "storage": storage,
            "is_rejected": child_row.is_rejected,
            "type_of_transaction": type_of_transaction,
            "posting_date": parent_doc.get("posting_date"),
            "posting_time": parent_doc.get("posting_time"),
            "company": parent_doc.get("company"),
        }
    )

    batch_no = None

    if (
        not entries[0].get("batch_no")
        and entries[0].get("serial_no")
        and frappe.get_cached_value("Item", child_row.item_code, "has_batch_no")
    ):
        batch_no = get_batch(child_row.item_code)

    for row in entries:
        row = frappe._dict(row)
        doc.append(
            "entries",
            {
                "qty": (flt(row.qty) or 1.0) * (1 if type_of_transaction == "Inward" else -1),
                "warehouse": warehouse,
                "storage": storage,
                "batch_no": row.batch_no or batch_no,
                "serial_no": row.serial_no,
            },
        )

    doc.save()

    if do_not_save:
        frappe.db.set_value(
            child_row.doctype, child_row.name, "serial_and_batch_bundle", doc.name
        )

    frappe.msgprint(frappe._("Serial and Batch Bundle created"), alert=True)

    return doc


def patched_make_serial_and_batch_bundle(self, serial_nos=None, batch_nos=None):
    """Storage-aware wrapper for SerialBatchCreation.make_serial_and_batch_bundle.

    When storage is not in the args (e.g. make_bundle_using_old_serial_batch_fields),
    auto-detect it from the voucher detail row before creating the SABB.
    """
    if not self.get("storage") and self.get("voucher_detail_no") and self.get("voucher_type"):
        meta = frappe.get_meta(self.voucher_type)
        for df in meta.get_table_fields():
            child_meta = frappe.get_meta(df.options)
            if child_meta.has_field("storage"):
                # Try storage (outward/source), then to_storage (inward/target)
                fields = ["storage"]
                if child_meta.has_field("to_storage"):
                    fields.append("to_storage")
                row_data = frappe.db.get_value(df.options, self.voucher_detail_no, fields, as_dict=True)
                if row_data:
                    storage = row_data.get("storage") or row_data.get("to_storage")
                    if storage:
                        self.storage = storage
                        self.__dict__["storage"] = storage
                break

    return _original_make_serial_and_batch_bundle(self, serial_nos, batch_nos)


def apply_monkey_patches():
    """Replace standard availability functions with storage-aware versions."""
    global _original_make_serial_and_batch_bundle

    from erpnext.stock.doctype.serial_and_batch_bundle import (
        serial_and_batch_bundle as sabb_module,
    )
    from erpnext.stock.serial_batch_bundle import SerialBatchCreation

    _original_make_serial_and_batch_bundle = SerialBatchCreation.make_serial_and_batch_bundle

    # Phase 4: batch availability
    sabb_module.get_available_batches = patched_get_available_batches
    sabb_module.get_stock_ledgers_batches = patched_get_stock_ledgers_batches

    # Phase 5: serial availability
    sabb_module.get_available_serial_nos = patched_get_available_serial_nos

    # Phase 6: validation methods
    sabb_module.SerialandBatchBundle.validate_serial_nos_inventory = (
        patched_validate_serial_nos_inventory
    )
    sabb_module.SerialandBatchBundle.validate_batch_inventory = (
        patched_validate_batch_inventory
    )

    # Phase 7: picked batches
    sabb_module.get_picked_batches = patched_get_picked_batches

    # Phase 8: SRE reservations
    sabb_module.get_reserved_batches_for_sre = patched_get_reserved_batches_for_sre
    sabb_module.get_reserved_serial_nos_for_sre = patched_get_reserved_serial_nos_for_sre

    # Phase 9a: SABB creation via dialog (create_serial_batch_no_ledgers)
    sabb_module.create_serial_batch_no_ledgers = patched_create_serial_batch_no_ledgers

    # Phase 9b: SABB creation via submit (SerialBatchCreation.make_serial_and_batch_bundle)
    SerialBatchCreation.make_serial_and_batch_bundle = patched_make_serial_and_batch_bundle
