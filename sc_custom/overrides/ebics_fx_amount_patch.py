"""
Foreign-currency camt entries for SC Custom.

When a payment is instructed in a currency other than the account currency
(e.g. a USD supplier payment from the CHF account), UBS books the entry in
CHF (`Ntry/Amt`) but reports the instructed USD amount in the transaction
details (`TxDtls/Amt`, `AmtDtls/InstdAmt`). fintech's SEPATransaction.amount
returns the TxDtls amount, so the Alyf banking app tries to create a Bank
Transaction in USD on a CHF Bank Account. ERPNext's
BankTransaction.validate_currency() rejects it, the banking app rolls back the
whole import, and NO transaction of that day is created — not even the plain
CHF/EUR ones. The EBICS Request log is already marked Successful by then, so
the day is never re-fetched. Seen on prod for 24.09 and 25.09.2026.

This patch wraps banking.ebics.utils.create_sepa_bank_transaction (the single
point used by the EBICS scheduler AND the manual camt.053 upload in the Bank
Reconciliation Tool). When the transaction currency differs from the Bank
Account's GL account currency, it climbs from the fintech TxDtls node to the
enclosing Ntry and uses the booked amount in account currency instead. The
instructed amount and exchange rate are appended to the description so the
accountant still sees them.

Safety rules:
- only applies when the Ntry holds exactly one TxDtls (a batch entry would
  otherwise get the whole batch amount for each sub-transaction);
- only applies when the Ntry currency equals the account currency;
- otherwise the transaction is passed through unchanged (Alyf's original
  behaviour, i.e. the ERPNext currency error).

After a banking app update, verify the patch still attaches:
    import banking.ebics.utils as u
    getattr(u.create_sepa_bank_transaction, "_sc_fx_amount", False)  # must be True
"""

from decimal import Decimal, InvalidOperation

import frappe
from lxml import etree

# Reference to the original function, set during apply_ebics_fx_amount_patch()
_original_create_sepa_bank_transaction = None


def _localname(element) -> str:
    tag = getattr(element, "tag", None)
    if not isinstance(tag, str):
        return ""
    return etree.QName(tag).localname


def _child(element, name):
    """Direct child of `element` with local tag `name`, ignoring namespaces."""
    if element is None:
        return None
    for child in element.iterchildren():
        if _localname(child) == name:
            return child
    return None


def _path(element, *names):
    for name in names:
        element = _child(element, name)
        if element is None:
            return None
    return element


def _text(element) -> str | None:
    if element is None:
        return None
    text = element.text
    return text.strip() if isinstance(text, str) and text.strip() else None


def _find_entry(xmlobj):
    """Return the enclosing camt `Ntry` element of a fintech transaction node, or None."""
    if xmlobj is None or not hasattr(xmlobj, "iterancestors"):
        return None
    if _localname(xmlobj) == "Ntry":
        return xmlobj
    for ancestor in xmlobj.iterancestors():
        if _localname(ancestor) == "Ntry":
            return ancestor
    return None


def get_booked_entry_amount(sepa_transaction, account_currency: str) -> dict | None:
    """Booked amount of the enclosing Ntry in account currency, or None if not applicable.

    Returns {"value": Decimal (signed like fintech: debits negative), "currency": str,
    "exchange_rate": str | None} or None.
    """
    entry = _find_entry(getattr(sepa_transaction, "_xmlobj", None))
    if entry is None:
        return None

    # A batch entry has many TxDtls; the Ntry amount is then the batch total.
    tx_details = [el for el in entry.iterdescendants() if _localname(el) == "TxDtls"]
    if len(tx_details) > 1:
        return None

    amount_el = _child(entry, "Amt")
    entry_currency = (amount_el.get("Ccy") or "").strip().upper() if amount_el is not None else ""
    if not entry_currency or entry_currency != account_currency:
        return None

    try:
        magnitude = abs(Decimal(_text(amount_el) or ""))
    except (InvalidOperation, TypeError, ValueError):
        return None

    # Keep the direction fintech determined for this transaction (debits negative).
    try:
        sign = -1 if Decimal(str(sepa_transaction.amount.value)) < 0 else 1
    except (InvalidOperation, TypeError, ValueError):
        sign = -1 if _text(_child(entry, "CdtDbtInd")) == "DBIT" else 1

    exchange_rate = _text(_path(entry, "AmtDtls", "CntrValAmt", "CcyXchg", "XchgRate"))

    return {
        "value": sign * magnitude,
        "currency": entry_currency,
        "exchange_rate": exchange_rate,
    }


class FXAdjustedTransaction:
    """Proxy around a fintech SEPATransaction that reports the booked account-currency amount.

    Everything else (iban, name, eref, purpose, _xmlobj, ...) is delegated to the original.
    """

    def __init__(self, sepa_transaction, booked: dict):
        from fintech.sepa import Amount

        self._sc_original = sepa_transaction
        self._sc_booked = booked
        self._sc_amount = Amount(str(booked["value"]), booked["currency"])

    def __getattr__(self, name):
        return getattr(self._sc_original, name)

    def __len__(self):
        return len(self._sc_original)

    def __iter__(self):
        return iter(self._sc_original)

    @property
    def amount(self):
        return self._sc_amount

    @property
    def purpose(self):
        """Original purpose lines plus a note with the instructed amount and rate."""
        original = self._sc_original
        lines = list(original.purpose or ())
        if not lines and original.info:
            # Alyf falls back to `info` when purpose is empty; keep it when we add a line.
            lines.append(original.info)
        lines.append(self.fx_note())
        return tuple(lines)

    def fx_note(self) -> str:
        instructed = self._sc_original.amount
        note = f"Original amount: {instructed.currency} {abs(Decimal(str(instructed.value)))}"
        rate = self._sc_booked.get("exchange_rate")
        if rate:
            note += f" (exchange rate {rate})"
        return note


def _get_bank_account_currency(bank_account: str) -> str | None:
    if not bank_account:
        return None
    account = frappe.get_cached_value("Bank Account", bank_account, "account")
    if not account:
        return None
    return frappe.get_cached_value("Account", account, "account_currency")


def patched_create_sepa_bank_transaction(bank_account, company, sepa_transaction, *args, **kwargs):
    account_currency = _get_bank_account_currency(bank_account)
    transaction_currency = getattr(getattr(sepa_transaction, "amount", None), "currency", None)

    if account_currency and transaction_currency and transaction_currency != account_currency:
        booked = get_booked_entry_amount(sepa_transaction, account_currency)
        if booked:
            sepa_transaction = FXAdjustedTransaction(sepa_transaction, booked)

    return _original_create_sepa_bank_transaction(bank_account, company, sepa_transaction, *args, **kwargs)


def apply_ebics_fx_amount_patch():
    global _original_create_sepa_bank_transaction

    try:
        import banking.ebics.utils as banking_utils

        original = banking_utils.create_sepa_bank_transaction
    except Exception:
        # banking app not installed, or its structure changed after an
        # update — the patch cannot attach. The print lands in bench and
        # worker logs; check it after every banking app update.
        print("sc_custom: EBICS FX amount patch NOT applied (banking app missing or changed)")
        return

    if getattr(original, "_sc_fx_amount", False):
        return  # already patched — safe on repeated imports

    _original_create_sepa_bank_transaction = original
    patched_create_sepa_bank_transaction._sc_fx_amount = True
    banking_utils.create_sepa_bank_transaction = patched_create_sepa_bank_transaction
