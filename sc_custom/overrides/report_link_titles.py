"""Show Link titles in Script Reports consistently.

For doctypes with "Show Title in Link Fields" (Project, Supplier, Customer) the report
grid shows a Link value's title only if the browser already has it cached
(frappe._link_titles, filled as a side effect of opening forms or Link dropdowns);
query_report.run never sends titles, so one row shows the project name and the next
the project code. This wrapper runs the standard method and adds the titles of the
Link values in the result to the response (_link_titles), which frappe.request merges
into that cache before the report is rendered.

Script Reports only; export and the other report types are left untouched.
Registered via override_whitelisted_methods — re-check the run() signature against
apps/frappe desk/query_report.py on each Frappe upgrade.
"""

import frappe
from frappe.desk import query_report
from frappe.desk.form.load import send_link_titles


@frappe.whitelist()
def run(
	report_name,
	filters=None,
	user=None,
	ignore_prepared_report=False,
	custom_columns=None,
	is_tree=False,
	parent_field=None,
	are_default_filters=True,
	js_filters=None,
):
	result = query_report.run(
		report_name,
		filters=filters,
		user=user,
		ignore_prepared_report=ignore_prepared_report,
		custom_columns=custom_columns,
		is_tree=is_tree,
		parent_field=parent_field,
		are_default_filters=are_default_filters,
		js_filters=js_filters,
	)

	if isinstance(result, dict) and (
		frappe.get_cached_value("Report", report_name, "report_type") == "Script Report"
	):
		try:
			link_titles = get_link_titles(result.get("columns") or [], result.get("result") or [])
		except Exception:
			# titles are cosmetic: never fail the report because of them
			frappe.log_error(title="Report link titles failed", reference_doctype="Report", reference_name=report_name)
			link_titles = None
		if link_titles:
			send_link_titles(link_titles)

	return result


def get_link_titles(columns, rows):
	"""Return {"<doctype>::<name>": title} for the values of Link columns whose
	doctype has "Show Title in Link Fields" enabled."""
	names_by_doctype = {}
	for idx, col in enumerate(columns):
		col = query_report.get_column_as_dict(col)
		doctype = col.get("options")
		if col.get("fieldtype") != "Link" or not doctype or not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		if not (meta.show_title_field_in_link and meta.title_field):
			continue

		names = names_by_doctype.setdefault(doctype, set())
		for row in rows:
			if isinstance(row, dict):
				value = row.get(col.fieldname)
			elif isinstance(row, list | tuple) and idx < len(row):
				value = row[idx]
			else:
				continue
			if value and isinstance(value, str):
				names.add(value)

	link_titles = {}
	for doctype, names in names_by_doctype.items():
		if not names:
			continue
		title_field = frappe.get_meta(doctype).title_field
		for name, title in frappe.get_all(
			doctype,
			filters={"name": ("in", list(names))},
			fields=["name", title_field],
			as_list=True,
			order_by=None,
		):
			if title:
				link_titles[f"{doctype}::{name}"] = title

	return link_titles
