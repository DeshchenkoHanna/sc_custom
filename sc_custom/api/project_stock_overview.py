"""Project Stock Overview — data for the Desk page of the same name.

For one Project the page shows every item that belongs to the project and, per item:

* **On stock**  — current balance over all warehouses and storages;
* **Reserved**  — balance in the project's own Storage(s), i.e. the Storage records whose
  name starts with the project code (company rule: goods received for a project are
  always put into the project storage);
* **On order**  — open Purchase Order qty (ordered − received) on lines carrying this project;
* **Requested** — open Material Request qty (type Purchase) on lines carrying this project
  that is not yet turned into a Purchase Order;
* **On order, other** — open Purchase Order qty on lines with another project or none.

Dates for the Schedule view: a Material Request line gives its *required by* date; a Purchase
Order line gives ``expected_delivery_date`` when filled (the updated/confirmed date) else
``schedule_date``; ``projects`` carries the planned end date of every project the rows touch.

An item belongs to the project when at least one of Reserved / On order / Requested is > 0.

Stock balances are NOT a plain ``sum(actual_qty)``: a Stock Reconciliation entry carries
the *new* balance in ``qty_after_transaction`` (its ``actual_qty`` can be 0), so the ledger
of every item + warehouse is walked in posting order and a reconciliation is treated as
"set balance"; the difference is booked to the storage of that entry. Walked this way the
warehouse totals equal ``Bin.actual_qty`` (verified on the whole v16site ledger).

Everything is returned in one payload (rows + the document lines behind them); the page
does the secondary filtering (warehouse, item group, search) and the rendering itself.
"""

import re

import frappe
from frappe import _
from frappe.utils import flt, now

# a Storage is a project storage when its name starts with a Project id such as "P-4002"
PROJECT_STORAGE_RE = re.compile(r"^(P-\d{4})(?!\d)")

PO_OPEN_CONDITION = """
	p.docstatus = 1
	and p.status not in ('Closed', 'Completed')
	and i.qty > i.received_qty
"""

MR_OPEN_CONDITION = """
	m.docstatus = 1
	and m.status not in ('Stopped', 'Cancelled')
	and m.material_request_type = 'Purchase'
	and i.stock_qty > ifnull(i.ordered_qty, 0) * ifnull(i.conversion_factor, 1)
"""


def get_storage_to_project() -> dict[str, str]:
	"""Map every project Storage name to its Project id."""
	projects = set(frappe.get_all("Project", pluck="name"))
	out = {}
	for storage in frappe.get_all("Storage", pluck="name"):
		m = PROJECT_STORAGE_RE.match(storage)
		if m and m.group(1) in projects:
			out[storage] = m.group(1)
	return out


@frappe.whitelist()
def get_data(project: str) -> dict:
	if not project:
		frappe.throw(_("Project is mandatory"))
	if not frappe.db.exists("Project", project):
		frappe.throw(_("Project {0} not found").format(project), frappe.DoesNotExistError)

	frappe.has_permission("Project", "read", project, throw=True)
	for doctype in ("Stock Ledger Entry", "Purchase Order", "Material Request"):
		frappe.has_permission(doctype, "read", throw=True)

	storage_to_project = get_storage_to_project()
	own_storages = [s for s, p in storage_to_project.items() if p == project]

	items = _project_items(project, own_storages)
	payload = {
		"project": project,
		"generated": now(),
		"storage_to_project": storage_to_project,
		"items": [],
	}
	if not items:
		payload["projects"] = _project_dates(project, {})  # the schedule axis needs the end date even for an empty table
		return payload

	meta = {
		r.name: r
		for r in frappe.get_all(
			"Item",
			filters={"name": ["in", items]},
			fields=["name", "item_name", "item_group", "stock_uom"],
		)
	}
	rows = {
		code: {
			"code": code,
			"name": meta[code].item_name if code in meta else code,
			"group": meta[code].item_group if code in meta else "",
			"uom": meta[code].stock_uom if code in meta else "",
			"stock": [],  # [{wh, storage, qty}]
			"reserved": {},  # {project: qty}  (balance in that project's storages)
			"po": [],  # open PO lines, all projects
			"mrl": [],  # open MR lines, all projects
		}
		for code in items
	}

	_add_stock_balances(rows, items, storage_to_project)
	_add_open_po_lines(rows, items)
	_add_open_mr_lines(rows, items)
	payload["projects"] = _project_dates(project, rows)

	# keep only items that still belong to the project (a storage emptied long ago does not count)
	payload["items"] = [
		row
		for row in rows.values()
		if flt(row["reserved"].get(project)) > 0
		or any(l["prj"] == project for l in row["po"])
		or any(l["prj"] == project for l in row["mrl"])
	]
	return payload


def _project_dates(project: str, rows: dict) -> dict:
	"""Planned end date of the selected project and of every other project the rows touch."""
	codes = {project}
	for row in rows.values():
		codes.update(row["reserved"].keys())
		codes.update(l["prj"] for l in row["po"] if l["prj"])
		codes.update(l["prj"] for l in row["mrl"] if l["prj"])
	return {
		r.name: {"end": str(r.expected_end_date) if r.expected_end_date else None}
		for r in frappe.get_all(
			"Project", filters={"name": ["in", list(codes)]}, fields=["name", "expected_end_date"]
		)
	}


def _project_items(project: str, own_storages: list[str]) -> list[str]:
	"""Item codes that touch the project: in its storage, on an open PO line or an open MR line."""
	items = set()
	if own_storages:
		items.update(
			frappe.get_all(
				"Stock Ledger Entry",
				filters={"is_cancelled": 0, "storage": ["in", own_storages]},
				pluck="item_code",
				distinct=True,
			)
		)
	items.update(
		frappe.db.sql_list(
			f"""
			select distinct i.item_code
			from `tabPurchase Order Item` i
			join `tabPurchase Order` p on p.name = i.parent
			where {PO_OPEN_CONDITION} and i.project = %s
			""",
			project,
		)
	)
	items.update(
		frappe.db.sql_list(
			f"""
			select distinct i.item_code
			from `tabMaterial Request Item` i
			join `tabMaterial Request` m on m.name = i.parent
			where {MR_OPEN_CONDITION} and i.project = %s
			""",
			project,
		)
	)
	return sorted(items)


def _add_stock_balances(rows: dict, items: list[str], storage_to_project: dict) -> None:
	"""Walk the ledger of every item + warehouse; reconciliations set the balance."""
	entries = frappe.db.sql(
		"""
		select item_code, warehouse, ifnull(storage, '') as storage,
			voucher_type, actual_qty, qty_after_transaction
		from `tabStock Ledger Entry`
		where is_cancelled = 0 and item_code in %(items)s
		order by item_code, warehouse, posting_datetime, creation
		""",
		{"items": items},
		as_dict=True,
	)

	balances = {}  # (item, warehouse) -> {storage: qty}
	running = {}  # (item, warehouse) -> warehouse balance so far
	for e in entries:
		key = (e.item_code, e.warehouse)
		before = running.get(key, 0.0)
		if e.voucher_type == "Stock Reconciliation":
			delta = flt(e.qty_after_transaction) - before
		else:
			delta = flt(e.actual_qty)
		running[key] = before + delta
		per_storage = balances.setdefault(key, {})
		per_storage[e.storage] = per_storage.get(e.storage, 0.0) + delta

	for (item_code, warehouse), per_storage in balances.items():
		row = rows[item_code]
		for storage, qty in per_storage.items():
			qty = flt(qty, 3)
			if abs(qty) < 0.0005:
				continue
			row["stock"].append({"wh": warehouse, "storage": storage, "qty": qty})
			project = storage_to_project.get(storage)
			if project:
				row["reserved"][project] = flt(row["reserved"].get(project, 0.0) + qty, 3)


def _add_open_po_lines(rows: dict, items: list[str]) -> None:
	lines = frappe.db.sql(
		f"""
		select i.item_code, i.parent as po,
			ifnull(nullif(p.supplier_name, ''), p.supplier) as sup,
			ifnull(i.project, '') as prj,
			(i.qty - i.received_qty) * ifnull(i.conversion_factor, 1) as qty,
			ifnull(i.expected_delivery_date, i.schedule_date) as date
		from `tabPurchase Order Item` i
		join `tabPurchase Order` p on p.name = i.parent
		where {PO_OPEN_CONDITION} and i.item_code in %(items)s
		order by i.parent, i.idx
		""",
		{"items": items},
		as_dict=True,
	)
	for l in lines:
		rows[l.item_code]["po"].append(
			{"po": l.po, "sup": l.sup, "prj": l.prj, "qty": flt(l.qty, 3), "date": str(l.date or "")}
		)


def _add_open_mr_lines(rows: dict, items: list[str]) -> None:
	lines = frappe.db.sql(
		f"""
		select i.item_code, i.parent as mr, ifnull(i.project, '') as prj,
			i.stock_qty - ifnull(i.ordered_qty, 0) * ifnull(i.conversion_factor, 1) as qty,
			i.schedule_date as date, m.status
		from `tabMaterial Request Item` i
		join `tabMaterial Request` m on m.name = i.parent
		where {MR_OPEN_CONDITION} and i.item_code in %(items)s
		order by i.parent, i.idx
		""",
		{"items": items},
		as_dict=True,
	)
	for l in lines:
		rows[l.item_code]["mrl"].append(
			{"mr": l.mr, "prj": l.prj, "qty": flt(l.qty, 3), "date": str(l.date or ""), "status": l.status}
		)
