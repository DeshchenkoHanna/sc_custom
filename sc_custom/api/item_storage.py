"""Stock per Storage (inventory dimension) for the Item form.

Feeds the "Storage Quantity" table rendered by public/js/item.js
(sc_custom.storage_quantity). Replaces the Server Script API
"get_stock_by_storage_for_item" (Server Script "Get Item Storage Quantities"),
moved into the app on 2026-11-12 so the feature lives in one place; the
Server Script can be deleted once this is deployed.

Bin carries no inventory dimension, so the balance per Storage is rebuilt from
Stock Ledger Entry: normal vouchers contribute actual_qty; a Stock
Reconciliation row without batch/serial contributes qty_after_transaction
minus the previous balance of the same item, warehouse and storage, exactly as
the Server Script did.
"""

import frappe

# Fieldname of the Storage inventory dimension on Stock Ledger Entry.
STORAGE_FIELD = "storage"


@frappe.whitelist()
def get_stock_by_storage_for_item(item_code=None):
	"""Return [{storage_location, actual_qty}] for every Storage holding stock of item_code.

	Rows without a Storage value are reported as "Not Specified"; only positive
	balances are returned, sorted by storage_location.
	"""
	if not item_code:
		return []

	frappe.has_permission("Item", "read", doc=item_code, throw=True)

	sf = STORAGE_FIELD
	return frappe.db.sql(
		f"""
		SELECT
			CASE
				WHEN `{sf}` IS NULL OR `{sf}` = '' THEN 'Not Specified'
				ELSE `{sf}`
			END AS storage_location,
			SUM(calculated_qty_diff) AS actual_qty
		FROM (
			SELECT
				`{sf}`,
				CASE
					WHEN voucher_type = 'Stock Reconciliation'
						AND (batch_no IS NULL OR batch_no = '' OR serial_no IS NULL OR serial_no = '')
					THEN
						COALESCE(qty_after_transaction, 0)
						- COALESCE((
							SELECT COALESCE(qty_after_transaction, 0)
							FROM `tabStock Ledger Entry` sle_prev
							WHERE sle_prev.item_code = sle.item_code
								AND sle_prev.warehouse = sle.warehouse
								AND (
									(COALESCE(sle.`{sf}`, '') = '' AND COALESCE(sle_prev.`{sf}`, '') = '')
									OR (sle.`{sf}` IS NOT NULL AND sle.`{sf}` != ''
										AND sle_prev.`{sf}` = sle.`{sf}`)
								)
								AND sle_prev.docstatus < 2
								AND sle_prev.is_cancelled = 0
								AND (sle_prev.posting_datetime < sle.posting_datetime
									OR (sle_prev.posting_datetime = sle.posting_datetime
										AND sle_prev.creation < sle.creation))
							ORDER BY sle_prev.posting_datetime DESC, sle_prev.creation DESC
							LIMIT 1
						), 0)
					ELSE COALESCE(actual_qty, 0)
				END AS calculated_qty_diff
			FROM `tabStock Ledger Entry` sle
			WHERE sle.item_code = %(item_code)s
				AND sle.docstatus < 2
				AND sle.is_cancelled = 0
			ORDER BY sle.posting_datetime, sle.creation
		) AS stock_calc
		GROUP BY storage_location
		HAVING SUM(calculated_qty_diff) > 0
		ORDER BY storage_location
		""",
		{"item_code": item_code},
		as_dict=1,
	)
