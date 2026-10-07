"""
Exchange rate warning for foreign-currency purchase documents (FEAT-218)

A PO, Purchase Receipt or Purchase Invoice in EUR/USD/GBP saved at exchange rate 1.00
takes its amounts into CHF one for one. The client fixes the rate on new documents
(public/js/exchange_rate_guard.js); this warns the user who saves when it is still 1.00.
Saving is never blocked.
"""

import frappe
from frappe import _
from frappe.utils import flt, format_date
from erpnext.setup.utils import get_exchange_rate

# erpnext AccountsController.check_conversion_rate shows this generic message on Purchase
# Invoice only. It is dropped in favour of the warning below, so the user sees one message.
ERPNEXT_RATE_ONE_MESSAGE = "Conversion rate is 1.00, but document currency is different from company currency"


def warn_foreign_currency_rate_one(doc, method=None):
    company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    if not doc.currency or doc.currency == company_currency or flt(doc.conversion_rate) != 1:
        return

    date = doc.get("posting_date") or doc.get("transaction_date")
    rate = flt(get_exchange_rate(doc.currency, company_currency, date, "for_buying"))

    generic = _(ERPNEXT_RATE_ONE_MESSAGE)
    for message in [m for m in frappe.local.message_log if m.get("message") == generic]:
        frappe.local.message_log.remove(message)

    # The rate of the day really is 1.00 (e.g. EUR/CHF at parity; rates are published with
    # 4 decimals): nothing to warn about. A failed lookup (0) still warns.
    if flt(rate, 4) == 1:
        return

    text = _("Exchange Rate is 1.00, but the currency is {0}: {0} amounts go into {1} one for one.").format(
        doc.currency, company_currency
    )
    if rate:
        text += "<br>" + _("Rate {0} → {1} on {2}: {3}").format(
            doc.currency, company_currency, format_date(date), rate
        )

    frappe.msgprint(text, title=_("Check Exchange Rate"), indicator="orange")
