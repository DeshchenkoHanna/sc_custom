"""
Silence false EBICS permission warnings for SC Custom.

Alyf's banking.ebics.utils.validated_perms runs before each H005 download and
logs a "Banking Warning" when its check tuple is not found verbatim in the
bank's HTD permission list. For UBS this is a false positive on every sync:
UBS returns the message name wrapped with its version (e.g.
{'@version': '08', '#text': 'camt.053'}) and serves camt.054 under service REP
while Alyf's request carries STM, so the plain tuple never matches even though
the download is authorised and succeeds (see ebics_btf_version_patch).

This patch wraps validated_perms to compare leniently — on admin order type and
message name, ignoring version and service. A genuinely missing permission
still falls through to Alyf's original warning, and an unauthorised download is
still rejected and logged as a real error by the bank round-trip.

After a banking app update, verify the patch still attaches:
    import banking.ebics.utils as u
    getattr(u.validated_perms, "_sc_perms_lenient", False)  # must be True
"""


def _msg_name(part):
    """Message name from a permitted/required entry element (str, or {'#text': ...})."""
    return part.get("#text") if isinstance(part, dict) else part


def _normalise(entry):
    """Reduce an H005 entry to (admin_order_type, message_name); pass strings through (H004)."""
    if isinstance(entry, tuple) and entry:
        return (entry[0], _msg_name(entry[-1]))
    return entry


def apply_ebics_perms_warning_patch():
    try:
        import banking.ebics.utils as ebics_utils

        original = ebics_utils.validated_perms
    except Exception:
        # banking app not installed, or its structure changed after an update —
        # the patch cannot attach. The print lands in bench and worker logs.
        print("sc_custom: EBICS perms warning patch NOT applied (banking app missing or changed)")
        return

    if getattr(original, "_sc_perms_lenient", False):
        return  # already patched — safe on repeated imports

    def patched_validated_perms(ebics_user, permitted_types, required_type):
        required = _normalise(required_type)
        if any(_normalise(p) == required for p in permitted_types):
            return  # authorised — suppress the false-positive warning
        # genuinely absent: keep Alyf's original advisory warning
        return original(ebics_user, permitted_types, required_type)

    patched_validated_perms._sc_perms_lenient = True
    ebics_utils.validated_perms = patched_validated_perms
