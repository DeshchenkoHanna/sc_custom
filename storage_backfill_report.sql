-- ============================================================
-- Storage Backfill Report (READ-ONLY, no changes to data)
-- Run on production to preview what the migration would do
-- ============================================================

-- ============================================================
-- 1. Serial and Batch Bundle: what storage would be set from SLE
-- ============================================================
SELECT
    sabb.name AS bundle,
    sabb.voucher_type,
    sabb.voucher_no,
    sabb.item_code,
    sabb.warehouse,
    sabb.storage AS current_storage,
    sle.storage AS storage_from_sle,
    sabb.type_of_transaction,
    sabb.posting_date
FROM `tabSerial and Batch Bundle` sabb
INNER JOIN `tabStock Ledger Entry` sle
    ON sle.serial_and_batch_bundle = sabb.name
WHERE sle.storage IS NOT NULL
  AND sle.storage != ''
ORDER BY sabb.posting_date DESC, sabb.name;

-- ============================================================
-- 2. Serial and Batch Entry: what storage would be set from parent
--    (only shows entries where parent SABB has storage from SLE)
-- ============================================================
SELECT
    sabe.name AS entry,
    sabe.parent AS bundle,
    sabe.serial_no,
    sabe.batch_no,
    sabe.warehouse,
    sabe.storage AS current_storage,
    sle.storage AS storage_from_sle
FROM `tabSerial and Batch Entry` sabe
INNER JOIN `tabStock Ledger Entry` sle
    ON sle.serial_and_batch_bundle = sabe.parent
WHERE sle.storage IS NOT NULL
  AND sle.storage != ''
ORDER BY sabe.parent, sabe.idx;

-- ============================================================
-- 3. Serial No: what storage would be set
--    Shows the latest inward SLE with storage for each serial
-- ============================================================
SELECT
    sn.name AS serial_no,
    sn.item_code,
    sn.warehouse,
    sn.storage AS current_storage,
    latest.storage AS storage_from_sle,
    latest.voucher_type,
    latest.voucher_no,
    latest.posting_datetime
FROM `tabSerial No` sn
INNER JOIN (
    SELECT
        sabe.serial_no,
        sle.storage,
        sle.voucher_type,
        sle.voucher_no,
        sle.posting_datetime,
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
WHERE sn.warehouse IS NOT NULL
  AND sn.warehouse != ''
ORDER BY sn.item_code, sn.name;

-- ============================================================
-- 4. Summary: counts by storage value
-- ============================================================
SELECT 'SABB' AS doctype, sle.storage, COUNT(*) AS cnt
FROM `tabSerial and Batch Bundle` sabb
INNER JOIN `tabStock Ledger Entry` sle
    ON sle.serial_and_batch_bundle = sabb.name
WHERE sle.storage IS NOT NULL AND sle.storage != ''
GROUP BY sle.storage

UNION ALL

SELECT 'Serial No' AS doctype, latest.storage, COUNT(*) AS cnt
FROM `tabSerial No` sn
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
WHERE sn.warehouse IS NOT NULL AND sn.warehouse != ''
GROUP BY latest.storage

ORDER BY doctype, storage;
