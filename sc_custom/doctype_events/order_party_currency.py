"""
Party currency warnings on orders, invoices and Payment Requests (FEAT-192)

A party without Billing Currency, or without a Default Accounts row for the company, gets the
company default Payables / Receivables account (CHF) from erpnext get_party_account. A Purchase
Order stores that currency as party_account_currency in validate (validate_supplier), on every
save and on submit, so a EUR / USD order submitted before the Supplier was set up kept CHF; a
Payment Request then requested the amount in CHF.

  Order            (Purchase / Sales Order) warns on save of a draft when the party has no Billing
                   Currency, or when its account for the company is in another currency than the
                   Billing Currency: Default Accounts row missing, or the party already booked in
                   another currency (ERPNext then keeps that account). Setting up the party before
                   Submit is enough: submit runs validate and takes the party's account again.
  Invoice          (Purchase / Sales Invoice) warns on save of a draft when Credit To / Debit To is
                   - in CHF while the invoice and the party's Billing Currency are EUR / USD / ...
                     (account chosen by hand, or invoice made before the party's row existed), or
                   - an advance account (invoices go to Payables / Receivables, the advance is
                     allocated with Get Advances, FEAT-224).
                   ERPNext accepts both, the account currency being the company currency.
  Payment Request  from an order: the incomplete party as above; from an invoice: the invoice's
                   account as above (the request takes party_account_currency from the invoice).

An order whose stored party_account_currency is older than the party's row is not checked here:
runway (liquidity.doctype_events.payment_request.set_party_account_currency) sets the request's
party_account_currency from the party's account on every draft save.
Valid setups give no warning: a CHF party with a EUR order, a party booked in CHF with a EUR
invoice on the EUR Payables account (FEAT-146).
Nothing is shown on submit (the message asks for a change before submitting) and nothing is
blocked. Replaces the Client Script "Currency for Payment Request".
"""

import frappe
from frappe import _
from frappe.utils import get_link_to_form
from erpnext.accounts.party import get_party_account, get_party_gle_currency
from erpnext.accounts.utils import get_account_currency

from sc_custom.doctype_events.party_currency import get_default_accounts

ACCOUNT_LABEL = {"Customer": "Receivables", "Supplier": "Payables"}
ORDER_PARTY = {"Purchase Order": "Supplier", "Sales Order": "Customer"}
# party type, account field, field label
INVOICE_ACCOUNT = {
    "Purchase Invoice": ("Supplier", "credit_to", "Credit To"),
    "Sales Invoice": ("Customer", "debit_to", "Debit To"),
}


def _party_gap(party_type, party, company, uses):
    """Warning about the party's currency setup, or None when it is complete. `uses` names what
    takes the party account ("payments of this Purchase Order use"). ERPNext takes the account of
    the party's accounting entries instead of its Default Accounts row when the two currencies
    differ (get_party_account), so a party booked in CHF stays on the CHF account although its
    row and Billing Currency say EUR."""
    billing = frappe.db.get_value(party_type, party, "default_currency")
    account = get_party_account(party_type, party, company)
    currency = get_account_currency(account) if account else None
    link = get_link_to_form(party_type, party)
    label = _(ACCOUNT_LABEL[party_type])
    if not billing:
        text = _("Set the Billing Currency in the {0} master {1}. Until then {2} the {3} account in {4}, which may not be correct.").format(
            _(party_type), link, uses, label, frappe.bold(currency))
        ask = _("Or ask the ERP team to check this {0}.")
    elif currency and currency != billing:
        history = get_party_gle_currency(party_type, party, company)
        if history and history != billing:
            text = _("{0} {1} is already booked in {2}, although its Billing Currency is {3}. {4} the {5} account in {2}, which may not be correct.").format(
                _(party_type), link, frappe.bold(history), frappe.bold(billing), uses[:1].upper() + uses[1:], label)
            ask = _("Ask the ERP team to check this {0}.")
        else:
            text = _("Set up Default Accounts in the {0} master {1} ({2}). Until then {3} the {4} account in {5}, which may not be correct.").format(
                _(party_type), link, frappe.bold(billing), uses, label, frappe.bold(currency))
            ask = _("Or ask the ERP team to check this {0}.")
    else:
        return None
    return text + "<br>" + ask.format(_(party_type))


def _is_advance_account(account, company):
    """Same advance accounts as the Default Accounts autofill (party_currency.get_default_accounts)."""
    defaults = frappe.get_cached_value(
        "Company", company, ["default_advance_paid_account", "default_advance_received_account"]) or []
    if account in defaults:
        return True
    if frappe.db.exists("Party Account", {"company": company, "advance_account": account}):
        return True
    name = (frappe.get_cached_value("Account", account, "account_name") or "").lower()
    return "advance" in name and "employee" not in name


def _invoice_issue(doctype, inv):
    """Problem with the invoice's Credit To / Debit To and the account to select, or None."""
    party_type, field, label = INVOICE_ACCOUNT[doctype]
    party, account = inv.get(party_type.lower()), inv.get(field)
    if not party or not account or not inv.get("company"):
        return None
    account_currency = get_account_currency(account)
    if _is_advance_account(account, inv.company):
        problem = _("{0} {1} is an advance account. An invoice is posted to the {2} account; the advance is allocated with Get Advances.").format(
            _(label), frappe.bold(account), _(ACCOUNT_LABEL[party_type]))
        currency = account_currency
    else:
        company_currency = frappe.get_cached_value("Company", inv.company, "default_currency")
        billing = frappe.db.get_value(party_type, party, "default_currency")
        if not (inv.currency != company_currency and account_currency == company_currency and billing == inv.currency):
            return None
        problem = _("The {0} is in {1} and the Billing Currency of {2} {3} is {1}, but {4} {5} is in {6}.").format(
            _(doctype), frappe.bold(inv.currency), _(party_type), get_link_to_form(party_type, party), _(label), frappe.bold(account),
            frappe.bold(account_currency))
        currency = inv.currency
    target = get_default_accounts(party_type, currency, inv.company).get("account")
    if target:
        fix = _("Select {0} in {1}").format(frappe.bold(target), _(label))
    else:
        fix = _("Select the {0} account in {1} in {2}").format(_(ACCOUNT_LABEL[party_type]), frappe.bold(currency), _(label))
    return frappe._dict(problem=problem, fix=fix)


def warn_order_party_currency(doc, method=None):
    if doc.docstatus != 0:
        return  # save of a draft only: on submit (docstatus 1 in validate) the advice comes too late
    party_type = ORDER_PARTY.get(doc.doctype)
    party = doc.get(party_type.lower()) if party_type else None
    if not party or not doc.company:
        return
    gap = _party_gap(party_type, party, doc.company, _("payments of this {0} use").format(_(doc.doctype)))
    if not gap:
        return
    frappe.msgprint(
        gap,
        title=_("Check Party Currency"),
        indicator="orange",
    )


def warn_invoice_party_account(doc, method=None):
    if doc.docstatus != 0:
        return  # save of a draft only, as for orders
    issue = _invoice_issue(doc.doctype, doc)
    if issue:
        frappe.msgprint(
            _("{0} {1} before submitting this {2}.").format(issue.problem, issue.fix, _(doc.doctype)),
            title=_("Check Party Account"),
            indicator="orange",
        )


def warn_payment_request_party_currency(doc, method=None):
    if doc.docstatus != 0 or doc.party_type not in ACCOUNT_LABEL or not doc.party or not doc.company:
        return
    if doc.reference_doctype in ORDER_PARTY:
        gap = _party_gap(doc.party_type, doc.party, doc.company, _("this Payment Request uses"))
        if gap:
            frappe.msgprint(
                gap,
                title=_("Check Party Currency"),
                indicator="orange",
            )
    elif doc.reference_doctype in INVOICE_ACCOUNT and doc.reference_name:
        _party_type, field, _label = INVOICE_ACCOUNT[doc.reference_doctype]
        inv = frappe.db.get_value(doc.reference_doctype, doc.reference_name,
                                  ["company", "currency", _party_type.lower(), field], as_dict=True)
        issue = _invoice_issue(doc.reference_doctype, inv) if inv else None
        if issue:
            frappe.msgprint(
                _("{0} {1}: {2} The invoice has to be corrected by the ERP team first, then create this Payment Request again.").format(
                    _(doc.reference_doctype), frappe.bold(doc.reference_name), issue.problem),
                title=_("Check Party Account"),
                indicator="orange",
            )
