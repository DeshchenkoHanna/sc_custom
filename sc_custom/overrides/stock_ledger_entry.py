import frappe
from frappe import _

from erpnext.stock.doctype.stock_ledger_entry.stock_ledger_entry import (
    InventoryDimensionNegativeStockError,
    StockLedgerEntry,
)


class CustomStockLedgerEntry(StockLedgerEntry):
    def throw_validation_error(self, diff, dimension_or_dimensions, dimension_value=None):
        item_label = f"[{self.item_code}] {frappe.get_desk_link('Item', self.item_code)}"

        row_info = ""
        if self.voucher_type == "Stock Entry" and self.voucher_detail_no:
            idx = frappe.db.get_value("Stock Entry Detail", self.voucher_detail_no, "idx")
            if idx:
                row_info = _(" at Row {0}").format(idx)

        # Support both old signature (diff, dimension, dimension_value)
        # and new signature (diff, dimensions_dict)
        if dimension_value is not None:
            dimension_text = f"{dimension_or_dimensions}: {dimension_value}"
        else:
            dimension_text = ", ".join(
                [f"{dimension}: {values.get('value')}" for dimension, values in dimension_or_dimensions.items()]
            )

        msg = _(
            "{0} units of {1} are required in {2}{3} with the inventory dimension: {4} on {5} {6} for {7} to complete the transaction."
        ).format(
            abs(diff),
            item_label,
            frappe.get_desk_link("Warehouse", self.warehouse),
            row_info,
            frappe.bold(dimension_text),
            self.posting_date,
            self.posting_time,
            frappe.get_desk_link(self.voucher_type, self.voucher_no),
        )

        frappe.throw(
            msg, title=_("Inventory Dimension Negative Stock"), exc=InventoryDimensionNegativeStockError
        )
