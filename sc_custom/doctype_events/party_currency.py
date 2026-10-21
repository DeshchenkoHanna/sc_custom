"""
Billing Currency guard and default-account autofill for Customer / Supplier (FEAT-224)

Why: ERPNext offers an advance in Get Advances only when the payment sits on the party's own
advance account, and it picks the party / advance account from the Party Account row of the
party. Parties in a foreign currency without that row get the CHF defaults, invoices land on
the wrong Payables account and advances become invisible. ERPNext also refuses a Party Account
row in a currency different from the party's existing accounting entries, so the Billing
Currency has to follow the history.

Runs in `before_validate` so that ERPNext's own checks (`validate_party_accounts`: one row per
company, currency of existing entries, Billing Currency vs account currency) still run on the
row this module adds.

Billing Currency itself is a mandatory field (Property Setter `reqd` on Customer.default_currency
and Supplier.default_currency, created by sc_custom.custom_fields on migrate); Frappe refuses an
empty one before this hook does anything.

Rules, per company (nothing here blocks a save; ERPNext's own checks still do):
  1. Billing Currency just changed to a currency outside ALL existing receivable / payable
     entries -> warning (message with OK). Usually the history currency stays; invoices in
     another currency are posted on that currency's Payables / Receivables account by hand,
     advances against the order on it. An unchanged Billing Currency is never questioned.
     No row is added in that case (ERPNext refuses a Default Account outside the history
     currency, the party keeps the Company defaults).
  2. Autofill: no Default Accounts row for the company -> a row is added with the Payables /
     Receivables and advance account of the Billing Currency (CHF parties included). An
     incomplete row gets the missing Company, Default Account or Default Advance Account.
  3. Accounts in another currency than the Billing Currency are refused by ERPNext itself
     (Customer / Supplier `validate_currency_for_receivable_payable_and_advance_account` and
     `validate_party_accounts`), so this module adds no check of its own for that.

Accounts per currency are resolved from the chart, first level with exactly one candidate wins:
  advance account  company default advance account -> advance accounts already used in Party
                   Account rows -> account named "...Advance..." (not Employee Advances)
  default account  company default receivable / payable account -> the single other non-group
                   Receivable / Payable account of that currency
Ambiguity -> nothing is filled and the user gets a message.
"""

import frappe
from frappe import _

ACCOUNT_TYPE = {"Customer": "Receivable", "Supplier": "Payable"}


def before_validate_party(doc, method=None):
    if doc.doctype not in ACCOUNT_TYPE or not doc.default_currency:
        return  # Billing Currency is a mandatory field (property setter); Frappe reports it
    before = doc.get_doc_before_save() if not doc.is_new() else None
    currency_changed = before is None or before.default_currency != doc.default_currency
    for company in frappe.get_all("Company", pluck="name"):
        _apply_for_company(doc, company, currency_changed)


def _apply_for_company(doc, company, currency_changed):
    # 1. a Billing Currency outside the accounting history: warn when it was just changed to that
    history = _history_currencies(doc.doctype, doc.name, company) if not doc.is_new() else set()
    if currency_changed and history and doc.default_currency not in history:
        frappe.msgprint(
            _(
                "Billing Currency {0} does not match the accounting entries of this {1}, which are in {2}. "
                "Usually {2} stays: invoices in {0} are posted on the {0} {3} account chosen on the invoice, and advances "
                "in {0} are paid against the order on that account. ERPNext will not accept a Default Account in {0} "
                "while entries exist in {2}."
            ).format(
                frappe.bold(doc.default_currency),
                _(doc.doctype),
                frappe.bold(", ".join(sorted(history))),
                _(ACCOUNT_TYPE[doc.doctype]),
            ),
            title=_("Billing Currency"),
            indicator="orange",
        )
    if history and doc.default_currency not in history:
        return  # ERPNext refuses a Default Account outside the history currency; nothing to fill

    # 2. Default Accounts row for the company: added when missing, completed when incomplete
    rows = doc.get("accounts") or []
    row = next((a for a in rows if a.company == company), None) or next((a for a in rows if not a.company), None)
    if row and row.company and row.account and row.advance_account:
        return
    accounts = get_default_accounts(doc.doctype, doc.default_currency, company)
    if not row:
        row = doc.append("accounts", {"company": company})
    if not row.company:
        row.company = company
    if not row.account and accounts.get("account"):
        row.account = accounts["account"]
    if not row.advance_account and accounts.get("advance_account"):
        row.advance_account = accounts["advance_account"]
    if not row.account or not row.advance_account:
        frappe.msgprint(
            _("No single {0} account in {1} found in the chart of accounts; complete the Default Accounts of this {2} by hand.").format(
                _(ACCOUNT_TYPE[doc.doctype]), doc.default_currency, _(doc.doctype)
            ),
            indicator="orange",
        )


def _history_currencies(party_type, party, company):
    rows = frappe.db.sql(
        """select distinct a.account_currency
           from `tabGL Entry` gl join `tabAccount` a on a.name = gl.account
           where gl.company = %s and gl.party_type = %s and gl.party = %s and gl.is_cancelled = 0
             and a.account_type in ('Receivable', 'Payable')""",
        (company, party_type, party),
    )
    return {r[0] for r in rows if r[0]}


@frappe.whitelist()
def get_default_accounts(party_type, currency, company=None):
    """Payables / Receivables account and advance account for a party type and currency.

    Returns {"account": ..., "advance_account": ...}; a key is missing when the chart holds
    no single candidate. Used by the Customer / Supplier form when the Billing Currency changes
    and by the before_validate hook.
    """
    if party_type not in ACCOUNT_TYPE or not currency:
        return {}
    company = company or frappe.defaults.get_global_default("company")
    if not company:
        return {}
    account_type = ACCOUNT_TYPE[party_type]
    candidates = frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0, "account_type": account_type, "account_currency": currency},
        fields=["name", "account_name"],
    )
    names = {a.name for a in candidates}
    company_defaults = frappe.get_cached_value(
        "Company",
        company,
        ["default_receivable_account", "default_payable_account", "default_advance_received_account", "default_advance_paid_account"],
        as_dict=True,
    )
    default_main = company_defaults.default_receivable_account if party_type == "Customer" else company_defaults.default_payable_account
    default_advance = (
        company_defaults.default_advance_received_account if party_type == "Customer" else company_defaults.default_advance_paid_account
    )
    used_advance = set(
        frappe.get_all("Party Account", filters={"company": company, "advance_account": ["is", "set"]}, pluck="advance_account")
    )
    by_name = [a.name for a in candidates if "advance" in a.account_name.lower() and "employee" not in a.account_name.lower()]

    def pick(*levels):
        """First level with exactly one candidate wins (company default -> used in Party Account -> by name)."""
        for level in levels:
            level = [n for n in level if n in names]
            if len(level) == 1:
                return level[0]
        return None

    result = {}
    advance = pick([default_advance], used_advance, by_name)
    main = pick([default_main], [n for n in names if n != advance and "advance" not in n.lower()])
    if main:
        result["account"] = main
    if advance:
        result["advance_account"] = advance
    return result
