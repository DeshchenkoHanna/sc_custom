"""
Patch: Backfill storage field on Serial and Batch Bundle, Entry, and Serial No.

Copies storage values from Stock Ledger Entries (populated by Inventory Dimension)
into the related Serial and Batch Bundle/Entry records and Serial No master.

This is a one-time migration for existing data. New transactions are handled
by the on_sle_after_insert hook (Phase 2+3 in serial_batch_storage.py).
"""

import frappe


def execute():
    # Skip if storage column doesn't exist yet on SLE (Inventory Dimension not set up)
    sle_columns = frappe.db.get_table_columns("Stock Ledger Entry")
    if "storage" not in sle_columns:
        return

    # Step 1: SABB ← SLE
    # Each bundle is linked to exactly one SLE via serial_and_batch_bundle
    sabb_updated = frappe.db.sql("""
        UPDATE `tabSerial and Batch Bundle` sabb
        INNER JOIN `tabStock Ledger Entry` sle
            ON sle.serial_and_batch_bundle = sabb.name
        SET sabb.storage = sle.storage
        WHERE (sabb.storage IS NULL OR sabb.storage = '')
          AND sle.storage IS NOT NULL
          AND sle.storage != ''
    """)
    sabb_count = frappe.db.sql("SELECT ROW_COUNT()")[0][0]

    # Step 2: SABE ← parent SABB
    # All child entries inherit storage from parent bundle
    sabe_updated = frappe.db.sql("""
        UPDATE `tabSerial and Batch Entry` sabe
        INNER JOIN `tabSerial and Batch Bundle` sabb
            ON sabe.parent = sabb.name
        SET sabe.storage = sabb.storage
        WHERE (sabe.storage IS NULL OR sabe.storage = '')
          AND sabb.storage IS NOT NULL
          AND sabb.storage != ''
    """)
    sabe_count = frappe.db.sql("SELECT ROW_COUNT()")[0][0]

    # Step 3: Serial No ← latest inward SLE
    # For serials currently in a warehouse, set storage from the latest receipt
    sn_updated = frappe.db.sql("""
        UPDATE `tabSerial No` sn
        INNER JOIN (
            SELECT
                sabe.serial_no,
                sle.storage,
                ROW_NUMBER() OVER (
                    PARTITION BY sabe.serial_no
                    ORDER BY sle.posting_datetime DESC, sle.creation DESC
                ) AS rn
            FROM `tabStock Ledger Entry` sle
            INNER JOIN `tabSerial and Batch Entry` sabe
                ON sabe.parent = sle.serial_and_batch_bundle
            WHERE sle.actual_qty > 0
              AND sle.storage IS NOT NULL
              AND sle.storage != ''
              AND sle.is_cancelled = 0
        ) latest
            ON latest.serial_no = sn.name
            AND latest.rn = 1
        SET sn.storage = latest.storage
        WHERE (sn.storage IS NULL OR sn.storage = '')
          AND sn.warehouse IS NOT NULL
          AND sn.warehouse != ''
    """)
    sn_count = frappe.db.sql("SELECT ROW_COUNT()")[0][0]

    frappe.db.commit()

    msg = (
        f"Storage backfill: {sabb_count} bundles, "
        f"{sabe_count} entries, {sn_count} serial nos updated"
    )
    frappe.logger().info(msg)
    print(msg)
