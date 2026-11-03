"""
Party currency warnings on orders and Payment Requests (FEAT-192)

A party without Billing Currency, or without a Default Accounts row for the company, gets the
company default Payables / Receivables account (CHF) from erpnext get_party_account. A Purchase
Order stores that currency as party_account_currency in validate (validate_supplier), on every
save and on submit, so a EUR / USD order submitted before the Supplier was set up kept CHF; a
Payment Request then requested the amount in CHF.

  Order            (Purchase / Sales Order) warns on save and submit when the party has no Billing
                   Currency, or when its account for the company is in another currency than the
                   Billing Currency (Default Accounts row missing). Setting up the party before
                   Submit is enough: submit runs validate and takes the party's account again.
  Payment Request  (from an order) warns on a draft for the same incomplete party.

An order whose stored party_account_currency is older than the party's row is not checked here:
runway (liquidity.doctype_events.payment_request.set_party_account_currency) sets the request's
party_account_currency from the party's account on every draft save.
A party in CHF with an order in EUR is a valid setup (FEAT-146) and gives no warning.
Saving is never blocked. Replaces the Client Script "Currency for Payment Request".
"""

import frappe
from frappe import _
from erpnext.accounts.party import get_party_account
from erpnext.accounts.utils import get_account_currency

ACCOUNT_LABEL = {"Customer": "Receivables", "Supplier": "Payables"}
ORDER_PARTY = {"Purchase Order": "Supplier", "Sales Order": "Customer"}


def _party_gap(party_type, party, company):
    """What to set up in the party master, or None when its currency setup is complete."""
    billing = frappe.db.get_value(party_type, party, "default_currency")
    account = get_party_account(party_type, party, company)
    currency = get_account_currency(account) if account else None
    if not billing:
        todo = _("Set the Billing Currency in the {0} master {1} first (Default Accounts are then filled automatically)").format(
            _(party_type), frappe.bold(party))
    elif currency and currency != billing:
        todo = _("Set up Default Accounts in the {0} master {1} first (Billing Currency {2})").format(
            _(party_type), frappe.bold(party), frappe.bold(billing))
    else:
        return None
    return frappe._dict(todo=todo, currency=currency)


def warn_order_party_currency(doc, method=None):
    party_type = ORDER_PARTY.get(doc.doctype)
    party = doc.get(party_type.lower()) if party_type else None
    if not party or not doc.company:
        return
    gap = _party_gap(party_type, party, doc.company)
    if not gap:
        return
    frappe.msgprint(
        _("{0}, then submit this {1}. Until then the {1} uses accounts in {2}, which may not be correct.").format(
            gap.todo, _(doc.doctype), frappe.bold(gap.currency)),
        title=_("Check Party Currency"),
        indicator="orange",
    )


def warn_payment_request_party_currency(doc, method=None):
    if doc.docstatus != 0 or doc.reference_doctype not in ORDER_PARTY or doc.party_type not in ACCOUNT_LABEL:
        return
    if not doc.party or not doc.company:
        return
    gap = _party_gap(doc.party_type, doc.party, doc.company)
    if gap:
        frappe.msgprint(
            _("{0}, then create this Payment Request again. Until then it uses accounts in {1}, which may not be correct.").format(
                gap.todo, frappe.bold(gap.currency)),
            title=_("Check Party Currency"),
            indicator="orange",
        )
