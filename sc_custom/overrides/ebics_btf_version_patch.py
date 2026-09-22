"""
EBICS H005 BTF version patch for SC Custom.

UBS (and Swiss banks generally) provision EBICS 3.0 (H005) download order
types with an explicit ISO-20022 version: camt.052/053/054 are served as
version 08 (the ISO-2019 / Swiss Payment Standards 2022 generation). The
Alyf banking app builds the BTF download request WITHOUT a version, so UBS
rejects it with EBICS_AUTHORISATION_ORDER_IDENTIFIER_FAILED.

Confirmed against the UBS production host on 2026-09-22: a camt.053 BTD with
version="08" (and NO variant) succeeds, while the version-less request fails
and variant="001" fails with EBICS_INVALID_ORDER_IDENTIFIER.

This patch wraps EBICSManager.download so that, for H005 Swiss camt
downloads, version="08" is added to the BusinessTransactionFormat. The
H004/H003 path, non-Swiss banks and uploads (pain.001) are left untouched.

When Switzerland retires the next ISO generation, revisit the "08" literal.
After a banking app update, verify the patch still attaches:
    from banking.ebics.manager import EBICSManager
    getattr(EBICSManager.download, "_sc_btf_version", False)  # must be True
"""

# ISO-20022 version UBS serves for CH camt statements over EBICS 3.0.
CH_CAMT_VERSION = "08"


def patched_download(self, request):
    client = self.get_client()

    if client.version != "H005":
        # H004 / H003 path unchanged — uses order_type (Z53 etc.).
        return client.download(request.order_type, request.start_date, request.end_date)

    from fintech.ebics import BusinessTransactionFormat

    # UBS requires the version to be stated for CH camt downloads. Leave
    # non-CH or non-camt requests as Alyf built them (version=None).
    version = None
    if self.country_code == "CH" and (request.camt_msg or "").startswith("camt."):
        version = CH_CAMT_VERSION

    btf = BusinessTransactionFormat(
        service=request.service,
        msg_name=request.camt_msg,
        scope=self.country_code,
        container="ZIP",
        version=version,
    )
    return client.BTD(btf, request.start_date, request.end_date)


def apply_ebics_btf_version_patch():
    try:
        from banking.ebics.manager import EBICSManager

        original = EBICSManager.download
    except Exception:
        # banking app not installed, or its structure changed after an
        # update — the patch cannot attach. The print lands in bench and
        # worker logs; check it after every banking app update.
        print("sc_custom: EBICS BTF version patch NOT applied (banking app missing or changed)")
        return

    if getattr(original, "_sc_btf_version", False):
        return  # already patched — safe on repeated imports

    patched_download._sc_btf_version = True
    EBICSManager.download = patched_download
