import json

import frappe
from frappe import _
from frappe.permissions import has_permission


def validate_filters_permissions(report_name, filters=None, user=None, js_filters=None):
	"""Replacement for frappe.desk.query_report.validate_filters_permissions.

	Frappe 16.15+ rejects report Link filters whose value the user cannot
	read. For filters linking to the DocType doctype (e.g. voucher_type in
	Serial and Batch Summary), only System Managers can ever pass: Custom
	DocPerms on DocType are ignored by Meta.set_custom_permissions, so the
	check cannot be satisfied via Role Permission Manager. DocType names are
	schema metadata, not document data, so those filters are skipped here.
	Everything else matches the original (frappe 16.18.3) — re-check this
	copy after frappe upgrades.
	"""
	if not filters:
		return

	if js_filters is None:
		js_filters = []

	if isinstance(js_filters, str):
		js_filters = json.loads(js_filters)

	if isinstance(filters, str):
		filters = json.loads(filters)

	report = frappe.get_doc("Report", report_name)

	for field in report.filters + js_filters:
		if hasattr(field, "as_dict"):
			field = field.as_dict()
		if field.get("fieldname") in filters and field.get("fieldtype") == "Link":
			linked_doctype = field.get("options")
			if linked_doctype == "DocType":
				continue
			if not has_permission(
				doctype=linked_doctype, ptype="read", doc=filters[field.get("fieldname")], user=user
			) and not has_permission(
				doctype=linked_doctype, ptype="select", doc=filters[field.get("fieldname")], user=user
			):
				frappe.throw(
					_("You do not have permission to access {0}: {1}.").format(
						linked_doctype, filters[field.get("fieldname")]
					)
				)


def apply_patch():
	from frappe.desk import query_report

	if getattr(query_report, "_sc_doctype_filter_patch_applied", False):
		return

	if hasattr(query_report, "validate_filters_permissions"):
		query_report.validate_filters_permissions = validate_filters_permissions

	query_report._sc_doctype_filter_patch_applied = True


apply_patch()
