"""
CRM full-visibility role for SC Custom.

Frappe CRM restricts CRM Lead / CRM Deal visibility per user in
`crm.permissions.org_hierarchy`: without System Manager (or Sales Manager
outside the Sales Hierarchy tree) a user only sees records they own, records
assigned to them, and — inside the tree — records of their subtree.

Read-only API twins (role "RO - ..." created by scripts/create_claude_reader_user.py)
must mirror a System Manager's CRM visibility WITHOUT getting System Manager or
Sales Manager, because those roles carry write/submit rights elsewhere.

This patch wraps the two private helpers of that module: a user holding the
role FULL_VISIBILITY_ROLE is treated like "Sales Manager outside the tree",
i.e. sees and may edit every lead and deal. The role itself carries no DocPerms,
so what the user may WRITE is still decided by Role Permission Manager.

After every crm app update, verify the patch still attaches:
    from crm.permissions import org_hierarchy
    getattr(org_hierarchy._has_permission, "_sc_crm_visibility", False)  # must be True
"""

import frappe

FULL_VISIBILITY_ROLE = "CRM Full Visibility"

_original_query_conditions = None
_original_has_permission = None


def _has_full_visibility(user: str | None) -> bool:
    user = user or frappe.session.user
    return FULL_VISIBILITY_ROLE in frappe.get_roles(user)


def patched_permission_query_conditions(user: str | None, doctype: str):
    if _has_full_visibility(user):
        return ""
    return _original_query_conditions(user, doctype)


def patched_has_permission(doc, ptype, user, doctype: str):
    if _has_full_visibility(user):
        return True
    return _original_has_permission(doc, ptype, user, doctype)


def apply_crm_visibility_patch():
    global _original_query_conditions, _original_has_permission

    try:
        from crm.permissions import org_hierarchy

        original_qc = org_hierarchy._permission_query_conditions
        original_hp = org_hierarchy._has_permission
    except Exception:
        # crm app not installed, or its structure changed after an update —
        # the patch cannot attach. Check bench/worker logs after every crm update.
        print("sc_custom: CRM visibility patch NOT applied (crm app missing or changed)")
        return

    if getattr(original_hp, "_sc_crm_visibility", False):
        return  # already patched — safe on repeated imports

    _original_query_conditions = original_qc
    _original_has_permission = original_hp

    patched_permission_query_conditions._sc_crm_visibility = True
    patched_has_permission._sc_crm_visibility = True
    org_hierarchy._permission_query_conditions = patched_permission_query_conditions
    org_hierarchy._has_permission = patched_has_permission
