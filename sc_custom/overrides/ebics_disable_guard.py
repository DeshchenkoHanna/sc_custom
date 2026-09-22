"""
EBICS disable guard for SC Custom.

The Alyf banking app checks the `disable_ebics` site_config flag only in its
two scheduler entry points (daily and intraday sync). The manual whitelisted
endpoints (Download Bank Statements, Initialize, Download Bank Keys, ...)
check only Banking Settings values from the DATABASE — and a prod backup
restore brings those back enabled, together with a live keyring and stored
passphrase. One click on a restored test site then talks to the real bank
and confirms statement downloads, so production misses those statements.

Every bank interaction in the banking app goes through
`EBICSManager.get_client()`, so this patch wraps that single choke point:
when `disable_ebics` is set in site_config.json, any attempt to communicate
with the bank fails with a clear message. Local key operations (INI letter
PDF, keyring handling) do not use get_client() and keep working.

On production (no flag) the wrapper is transparent.

After every banking app update, verify the guard still attaches:
    from banking.ebics.manager import EBICSManager
    getattr(EBICSManager.get_client, "_sc_ebics_guard", False)  # must be True
"""

import frappe
from frappe import _

# Reference to the original method, set during apply_ebics_disable_guard()
_original_get_client = None


def guarded_get_client(self):
    if frappe.conf.get("disable_ebics"):
        frappe.throw(
            _(
                "EBICS bank communication is blocked on this site "
                "('disable_ebics' is set in site_config.json). "
                "This is expected on test sites restored from production."
            )
        )
    return _original_get_client(self)


def apply_ebics_disable_guard():
    global _original_get_client

    try:
        from banking.ebics.manager import EBICSManager

        original = EBICSManager.get_client
    except Exception:
        # banking app not installed, or its structure changed after an
        # update — the guard cannot attach. The print lands in bench and
        # worker logs; check it after every banking app update.
        print("sc_custom: EBICS disable guard NOT applied (banking app missing or changed)")
        return

    if getattr(original, "_sc_ebics_guard", False):
        return  # already patched — safe on repeated imports

    _original_get_client = original
    guarded_get_client._sc_ebics_guard = True
    EBICSManager.get_client = guarded_get_client
