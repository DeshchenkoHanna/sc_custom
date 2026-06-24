"""
Fibery item sync — transactional outbox.

Pushes Item Code + Item Name (+ ERP modified timestamp) from ERPNext to a
Fibery database via the Fibery Commands API
(``fibery.entity.batch/create-or-update``).

Why this lives in an app method instead of a Server Script:
Frappe Server Scripts run under RestrictedPython, where ``frappe.conf`` is
NOT exposed and ``import requests`` is blocked. App code has no such
restriction, so the Fibery host/token stay in ``site_config.json`` (never
in the DB, never committed) and are read here with ``frappe.conf.get``.

Delivery model (see plan): two producers feed a single durable outbox
DocType ``Fibery Sync Queue``; one scheduler drainer (:func:`flush_queue`)
delivers and DELETES on success. Producers: the Item ``on_update`` hook
(:mod:`sc_custom.fibery_sync.item_events`) and the nightly
:func:`reconcile`. The queue is a work list, not a log.

site_config.json keys:
    fibery_host    e.g. "youraccount.fibery.io"   (no scheme)
    fibery_token   Fibery API token
    fibery_space   optional, default "ERP Dev"
    fibery_db      optional, default "Test-Items"

Field names are NOT in site_config — they live as module constants
(``FIBERY_ITEM_CODE_FIELD``, ``FIBERY_MODIFIED_FIELD``,
``FIBERY_DESCRIPTION_FIELD``) right below this docstring. Rename a field
in Fibery → edit the matching constant here. All three are plain Text
fields that MUST exist in the Fibery database; ``FIBERY_DESCRIPTION_FIELD``
is intentionally NOT the built-in rich-text "Description".
"""

import json
import uuid

import requests

import frappe
from frappe.utils import now

FIBERY_TIMEOUT = 60
OUTBOX = "Fibery Sync Queue"

# Fibery target field names. Centralised here so a Fibery rename is a
# single-line edit in code, not a per-site site_config.json change.
# Each field MUST exist in the Fibery database with the type noted below.
FIBERY_ITEM_CODE_FIELD = "ITM n°"                  # fibery/text — Item Code (conflict-field)
FIBERY_MODIFIED_FIELD = "ERP Modified"             # fibery/text — ERP modified timestamp
FIBERY_DESCRIPTION_FIELD = "ERP Description"       # fibery/text — HTML-stripped description
FIBERY_VALUATION_FIELD = "Valuation rate"          # fibery/int — Item.valuation_rate (rounded)
FIBERY_MAIN_SUPPLIER_FIELD = "Main supplier"       # fibery/text — first Item Supplier row, supplier ID
FIBERY_MAIN_SUPPLIER_PART_FIELD = "Main supplier part n°"  # fibery/text — first Item Supplier row, supplier_part_no
FIBERY_HAS_BOM_FIELD = "Has active BOM"            # fibery/bool — has at least one active+submitted BOM
FIBERY_HAS_PDF_FIELD = "Has pdf attached"          # fibery/bool — has at least one attached file with .pdf extension
FIBERY_HAS_SERIAL_OR_BATCH_FIELD = "Has serial or batch n°"  # fibery/bool — has_serial_no OR has_batch_no
FIBERY_ITEM_GROUP_FIELD = "Item group"             # fibery single-select — option name matched dynamically

# Only items whose item_group descends recursively from one of these
# roots are synced to Fibery. Items in any other group are silently
# ignored — they will not be enqueued by the on_update hook, the
# nightly reconcile, the enqueue_all seeder, or the sync_items test.
# The resolved set is cached in Frappe's cache for 5 minutes so a tree
# reshuffle in ERP becomes visible without a process restart.
SYNC_ITEM_GROUP_ROOTS = ("Manufacturing", "Product Parts")

# Stable namespace so a given item_code always maps to the same fibery/id.
# Fibery requires fibery/id on every entity in a create-or-update batch;
# a deterministic id keeps re-runs idempotent.
_FIBERY_ID_NAMESPACE = uuid.UUID("6f1e7c2a-4b3d-5e6f-8a9b-0c1d2e3f4a5b")


def _fibery_id(item_code):
	"""Deterministic UUID for an item_code (uuid5 over a fixed namespace)."""
	return str(uuid.uuid5(_FIBERY_ID_NAMESPACE, item_code))


def _get_conf():
	"""Read and validate Fibery connection settings from site_config.json.

	Returns (host, token, space, database). Field names live in module
	constants (FIBERY_*_FIELD), not in site_config. Raises (frappe.throw)
	if host/token are missing.
	"""
	host = frappe.conf.get("fibery_host")
	token = frappe.conf.get("fibery_token")
	if not host or not token:
		frappe.throw(
			"Fibery is not configured. Add 'fibery_host' and 'fibery_token' "
			"to site_config.json."
		)

	# Tolerate a host accidentally saved with a scheme.
	host = host.replace("https://", "").replace("http://", "").strip("/")

	space = frappe.conf.get("fibery_space") or "ERP Dev"
	database = frappe.conf.get("fibery_db") or "ERP-ITM"
	return host, token, space, database


def _plain_text(html):
	"""Item.description is a Text Editor (HTML); Fibery target is plain text."""
	if not html:
		return ""
	return frappe.utils.strip_html_tags(html).strip()


def _allowed_item_groups():
	"""Resolve SYNC_ITEM_GROUP_ROOTS to the full set of allowed group
	names (roots + all descendants, regardless of ``is_group``).

	Whether a group is itself a folder (``is_group = 1``) is irrelevant
	for filtering — what matters is the value stored on the Item. ERPNext
	may or may not allow picking a group itself; we sync whatever is
	there. Missing roots are silently skipped. Result is cached in
	Frappe's cache for 5 minutes so a tree reshuffle in ERP becomes
	visible without a process restart.
	"""
	cache_key = "fibery_sync_allowed_item_groups"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return set(cached)

	from frappe.utils.nestedset import get_descendants_of

	allowed = set()
	for root in SYNC_ITEM_GROUP_ROOTS:
		if not frappe.db.exists("Item Group", root):
			continue
		allowed.add(root)
		allowed.update(get_descendants_of("Item Group", root) or [])

	frappe.cache().set_value(cache_key, list(allowed), expires_in_sec=300)
	return allowed


def _fibery_item_group_options():
	"""Cached ``{option_name: fibery/id}`` map for the Fibery 'Item group'
	Single Select on the target database.

	A Single Select in Fibery is a reference to an entity in an enum
	type; the API accepts it as ``{"fibery/id": "<uuid>"}``, NOT as a
	plain string. We fetch the (name, fibery/id) pairs once every 5
	minutes and cache them so per-item lookups during a flush are free.
	If the fetch fails (Fibery unreachable, schema changed, etc.) we
	return an empty dict — callers then omit the Item group field, which
	is safer than guessing.
	"""
	cache_key = "fibery_sync_item_group_options"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return dict(cached)

	options = {}
	try:
		host, token, space, database = _get_conf()
		# Fibery's naming convention for a single-select enum type:
		# "<space>/<field_name>_<space>/<database>"
		enum_type = f"{space}/{FIBERY_ITEM_GROUP_FIELD}_{space}/{database}"
		cmd = [{
			"command": "fibery.entity/query",
			"args": {"query": {
				"q/from": enum_type,
				"q/select": ["fibery/id", "enum/name"],
				"q/limit": 1000,
			}},
		}]
		status, body = _post_to_fibery(host, token, cmd)
		if _is_success(status, body):
			for r in body[0].get("result") or []:
				name = r.get("enum/name")
				fid = r.get("fibery/id")
				if name and fid:
					options[name] = fid
	except Exception:
		# Don't break sync over a transient schema-fetch failure.
		options = {}

	# Cache empty too — short TTL means we'll retry within 5 min.
	frappe.cache().set_value(cache_key, options, expires_in_sec=300)
	return options


def _is_syncable(item_code):
	"""True iff the Item's item_group is covered by SYNC_ITEM_GROUP_ROOTS."""
	group = frappe.db.get_value("Item", item_code, "item_group")
	return bool(group) and group in _allowed_item_groups()


def _extra_fields(item_code):
	"""Per-item derived data not stored directly on the Item doctype.

	- ``supplier`` / ``supplier_part_no``: first row (by idx) of the
	  Item's child table ``Item Supplier`` (empty strings if no rows).
	- ``has_active_bom``: True iff at least one BOM exists for this item
	  with ``is_active=1`` and ``docstatus=1``.
	- ``has_pdf``: True iff at least one File is attached to this Item
	  whose ``file_name`` ends with ``.pdf`` (case-insensitive). Extension
	  check only — no MIME sniffing.

	NOTE on freshness: changes that don't bump ``Item.modified`` (creating
	a new BOM, attaching a file, stock movements changing valuation_rate)
	won't auto-re-enqueue the item. Item must be saved (or the nightly
	reconcile drift-check picks it up only if ``modified`` itself changed).
	"""
	sup_rows = frappe.get_all(
		"Item Supplier",
		filters={"parent": item_code, "parenttype": "Item"},
		fields=["supplier", "supplier_part_no"],
		order_by="idx asc",
		limit_page_length=1,
	)
	sup = sup_rows[0] if sup_rows else {}

	has_active_bom = bool(frappe.db.exists("BOM", {
		"item": item_code, "is_active": 1, "docstatus": 1,
	}))

	has_pdf = bool(frappe.db.sql(
		"""
		select 1 from `tabFile`
		where attached_to_doctype = 'Item'
		  and attached_to_name = %s
		  and lower(file_name) like %s
		limit 1
		""",
		(item_code, "%.pdf"),
	))

	return {
		"supplier": sup.get("supplier") or "",
		"supplier_part_no": sup.get("supplier_part_no") or "",
		"has_active_bom": has_active_bom,
		"has_pdf": has_pdf,
	}


def _entity_payload(i, space):
	"""Build one Fibery entity dict for the given Item dict.

	NB: ``Item group`` (Single Select) is NOT set here. Fibery's batch
	create-or-update silently ignores Single Select values (the field is
	modelled internally as a back-reference collection on the enum type).
	The Item group value is set separately via collection commands in
	:func:`_build_upsert_command`.
	"""
	extra = _extra_fields(i.item_code)
	return {
		"fibery/id": _fibery_id(i.item_code),
		f"{space}/{FIBERY_ITEM_CODE_FIELD}": i.item_code,
		f"{space}/Name": i.item_name,
		f"{space}/{FIBERY_MODIFIED_FIELD}": str(i.modified),
		f"{space}/{FIBERY_DESCRIPTION_FIELD}": _plain_text(i.description),
		f"{space}/{FIBERY_VALUATION_FIELD}": int(round(i.valuation_rate or 0)),
		f"{space}/{FIBERY_MAIN_SUPPLIER_FIELD}": extra["supplier"],
		f"{space}/{FIBERY_MAIN_SUPPLIER_PART_FIELD}": extra["supplier_part_no"],
		f"{space}/{FIBERY_HAS_BOM_FIELD}": extra["has_active_bom"],
		f"{space}/{FIBERY_HAS_PDF_FIELD}": extra["has_pdf"],
		f"{space}/{FIBERY_HAS_SERIAL_OR_BATCH_FIELD}":
			bool(i.has_serial_no or i.has_batch_no),
	}


def _item_group_commands(items, space, database):
	"""Emit the collection commands needed to set the Item group Single
	Select for the given Items.

	For each Item whose item_group maps to a known Fibery option, emit
	a remove-collection-items (sweeping ALL known options off the entity,
	idempotent for absent options) followed by an add-collection-items
	for the target. This is required because Fibery's Single Select is
	implemented as a collection internally — repeated ``add`` accumulates,
	so the old option must be explicitly removed first.

	Items whose item_group is not in the live Fibery option set emit no
	commands — the field on Fibery side is left untouched.
	"""
	options = _fibery_item_group_options()
	if not options:
		return []
	db_type = f"{space}/{database}"
	ig_field = f"{space}/{FIBERY_ITEM_GROUP_FIELD}"
	all_option_items = [{"fibery/id": v} for v in options.values()]

	cmds = []
	for i in items:
		target_id = options.get(i.item_group) if i.item_group else None
		if not target_id:
			continue
		entity_ref = {"fibery/id": _fibery_id(i.item_code)}
		cmds.append({
			"command": "fibery.entity/remove-collection-items",
			"args": {
				"type": db_type,
				"entity": entity_ref,
				"field": ig_field,
				"items": all_option_items,
			},
		})
		cmds.append({
			"command": "fibery.entity/add-collection-items",
			"args": {
				"type": db_type,
				"entity": entity_ref,
				"field": ig_field,
				"items": [{"fibery/id": target_id}],
			},
		})
	return cmds


def _build_upsert_command(items, space, database):
	"""Build the full Fibery command list to upsert the given items.

	Returns a list of commands sent in a single ``/api/commands`` POST:
	one ``fibery.entity.batch/create-or-update`` for all scalar fields,
	followed by pairs of ``remove-collection-items`` /
	``add-collection-items`` per item to set the Single Select
	``Item group`` (see :func:`_item_group_commands`).
	"""
	cmds = [
		{
			"command": "fibery.entity.batch/create-or-update",
			"args": {
				"type": f"{space}/{database}",
				"entities": [_entity_payload(i, space) for i in items],
				"conflict-field": f"{space}/{FIBERY_ITEM_CODE_FIELD}",
				"conflict-action": "update-latest",
			},
		}
	]
	cmds.extend(_item_group_commands(items, space, database))
	return cmds


def _post_to_fibery(host, token, commands):
	"""POST commands to the Fibery Commands API and return (status, body)."""
	response = requests.post(
		f"https://{host}/api/commands",
		headers={
			"Authorization": f"Token {token}",
			"Content-Type": "application/json",
		},
		data=json.dumps(commands),
		timeout=FIBERY_TIMEOUT,
	)
	try:
		body = response.json()
	except ValueError:
		body = response.text
	return response.status_code, body


def _is_success(status, body):
	"""HTTP 200 and every command in the response returned ``success: true``.

	Fibery's ``/api/commands`` runs multiple commands sequentially and
	returns one result per command. We require all of them to succeed.
	"""
	return (
		status == 200
		and isinstance(body, list)
		and body
		and all(r.get("success") is True for r in body)
	)


def _classify_error(status, body, exc):
	"""Map a failure to a short, enumerable code + truncated text.

	Codes: CONFIG, TIMEOUT, CONN, HTTP_<code>, FIBERY:<name>, UNKNOWN.
	"""
	if exc is not None:
		if isinstance(exc, requests.exceptions.Timeout):
			return "TIMEOUT", str(exc)[:140]
		if isinstance(exc, requests.exceptions.ConnectionError):
			return "CONN", str(exc)[:140]
		if isinstance(exc, frappe.exceptions.ValidationError):
			return "CONFIG", str(exc)[:140]
		return "UNKNOWN", str(exc)[:140]

	if status != 200:
		detail = ""
		if isinstance(body, dict):
			detail = body.get("name") or body.get("message") or ""
		return f"HTTP_{status}", (detail or str(body))[:140]

	if isinstance(body, list) and body:
		# Find the first failing command in the response and surface it.
		for r in body:
			if r.get("success") is False:
				res = r.get("result")
				res = res if isinstance(res, dict) else {}
				name = res.get("name") or r.get("name") or "unknown"
				msg = res.get("message") or str(r)
				return f"FIBERY:{name}", msg[:140]

	return "UNKNOWN", str(body)[:140]


# --------------------------------------------------------------------------
# Producers — put an item_code into the outbox (idempotent get-or-create)
# --------------------------------------------------------------------------


def enqueue_item(item_code):
	"""Get-or-create an outbox row for ``item_code``.

	Called from the Item on_update hook (in the Item's transaction) and from
	reconcile(). Never raises out (caller wraps), but kept side-effect-cheap:
	no network here — only a local INSERT/UPDATE. The autoname is
	field:item_code so the document name == item_code, giving DB-level
	dedup (one open row per item).

	Items whose ``item_group`` is outside SYNC_ITEM_GROUP_ROOTS (recursively)
	are silently ignored.
	"""
	if not item_code or not _is_syncable(item_code):
		return

	if frappe.db.exists(OUTBOX, item_code):
		row = frappe.get_doc(OUTBOX, item_code)
		if row.status == "Error":
			# Re-arm a stuck row instead of creating a duplicate.
			row.update(
				{
					"status": "Not Sent",
					"retry": 0,
					"error_code": None,
					"last_error": None,
				}
			)
			row.save(ignore_permissions=True)
		return

	frappe.get_doc(
		{"doctype": OUTBOX, "item_code": item_code, "status": "Not Sent"}
	).insert(ignore_permissions=True)


@frappe.whitelist()
def enqueue_all():
	"""Idempotent seeder: enqueue every Item in the allowed item-group
	tree (SYNC_ITEM_GROUP_ROOTS). Run once after install."""
	allowed = _allowed_item_groups()
	if not allowed:
		return {"enqueued": 0,
		        "warning": "no item groups matched SYNC_ITEM_GROUP_ROOTS"}
	count = 0
	for item_code in frappe.get_all(
		"Item", filters={"item_group": ["in", list(allowed)]}, pluck="name"
	):
		enqueue_item(item_code)
		count += 1
	frappe.db.commit()
	return {"enqueued": count}


@frappe.whitelist()
def requeue_failed():
	"""Reset all Error rows back to Not Sent (manual reanimation)."""
	frappe.db.sql(
		"""
		update `tabFibery Sync Queue`
		set status='Not Sent', retry=0, error_code=NULL, last_error=NULL
		where status='Error'
		"""
	)
	frappe.db.commit()
	return {"requeued": frappe.db.count(OUTBOX, {"status": "Not Sent"})}


# --------------------------------------------------------------------------
# Drainer — the only thing that talks to Fibery (scheduler, every 5 min)
# --------------------------------------------------------------------------


def _pick_batch(batch):
	"""Rows to attempt: Not Sent, Error past backoff, or stale Sending.

	Backoff for Error rows grows with retry: min(retry,12)*5 minutes.
	Sending rows older than 30 min are treated as crashed mid-flight.
	"""
	return frappe.db.sql(
		"""
		select name, item_code
		from `tabFibery Sync Queue`
		where
			status = 'Not Sent'
			or (status = 'Error' and (
				last_attempt is null
				or last_attempt < date_sub(%(now)s,
					interval least(retry, 12) * 5 minute)
			))
			or (status = 'Sending' and last_attempt is not null
				and last_attempt < date_sub(%(now)s, interval 30 minute))
		order by retry asc, modified asc
		limit %(batch)s
		""",
		{"now": now(), "batch": int(batch)},
		as_dict=True,
	)


def flush_queue(batch=100):
	"""Scheduler drainer: deliver queued items to Fibery.

	Success (HTTP 200 + body success) -> DELETE the row (queue, not log).
	Failure -> status=Error, retry++, error_code, last_error, log_error.
	Each row is isolated and committed independently.
	"""
	try:
		host, token, space, database = _get_conf()
	except frappe.exceptions.ValidationError:
		# Not configured — nothing we can do; surfaced once in the log.
		frappe.log_error(title="Fibery Sync", message="Fibery not configured")
		return

	rows = _pick_batch(batch)
	for r in rows:
		name = r["name"]
		try:
			frappe.db.set_value(
				OUTBOX, name, {"status": "Sending", "last_attempt": now()},
				update_modified=False,
			)
			frappe.db.commit()

			item = frappe.db.get_value(
				"Item", r["item_code"],
				["item_code", "item_name", "modified", "description",
				 "valuation_rate", "has_serial_no", "has_batch_no",
				 "item_group"],
				as_dict=True,
			)
			if not item:
				# Item deleted after enqueue — drop the work item.
				frappe.delete_doc(OUTBOX, name, ignore_permissions=True, force=True)
				frappe.db.commit()
				continue

			commands = _build_upsert_command([item], space, database)
			status, body = _post_to_fibery(host, token, commands)

			if _is_success(status, body):
				frappe.delete_doc(OUTBOX, name, ignore_permissions=True, force=True)
				frappe.db.commit()
				continue

			code, text = _classify_error(status, body, None)
		except Exception as exc:  # noqa: BLE001 — isolate per row
			code, text = _classify_error(None, None, exc)

		# Failure path: keep the row, record the coded error, back off.
		row = frappe.get_doc(OUTBOX, name)
		row.update(
			{
				"status": "Error",
				"retry": (row.retry or 0) + 1,
				"error_code": code,
				"last_error": text,
				"last_attempt": now(),
			}
		)
		row.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.log_error(
			title="Fibery Sync",
			message=f"{r['item_code']}: {code}\n{text}",
		)


# --------------------------------------------------------------------------
# Reconcile — nightly second producer (full drift sweep into the outbox)
# --------------------------------------------------------------------------


def _fibery_snapshot(host, token, space, database):
	"""Page through Fibery and return {item_code: modified_str}."""
	snapshot = {}
	offset = 0
	page = 1000
	ic_field = f"{space}/{FIBERY_ITEM_CODE_FIELD}"
	md_field = f"{space}/{FIBERY_MODIFIED_FIELD}"
	while True:
		commands = [
			{
				"command": "fibery.entity/query",
				"args": {
					"query": {
						"q/from": f"{space}/{database}",
						"q/select": [ic_field, md_field],
						"q/limit": page,
						"q/offset": offset,
					}
				},
			}
		]
		status, body = _post_to_fibery(host, token, commands)
		if not _is_success(status, body):
			frappe.log_error(
				title="Fibery Sync",
				message=f"reconcile query failed: HTTP {status}\n{str(body)[:300]}",
			)
			return None
		result = body[0].get("result") or []
		for ent in result:
			code = ent.get(ic_field)
			if code:
				snapshot[code] = ent.get(md_field)
		if len(result) < page:
			break
		offset += page
	return snapshot


def reconcile():
	"""Nightly: enqueue any Item missing in Fibery or whose stored ERP
	modified timestamp differs from the current one. Only enqueues — the
	drainer delivers. Items outside SYNC_ITEM_GROUP_ROOTS are skipped."""
	host, token, space, database = _get_conf()

	fib = _fibery_snapshot(host, token, space, database)
	if fib is None:
		return  # Fibery unreachable/misconfigured; logged in snapshot.

	allowed = _allowed_item_groups()
	if not allowed:
		return {"checked": len(fib), "enqueued": 0,
		        "warning": "no item groups matched SYNC_ITEM_GROUP_ROOTS"}

	enqueued = 0
	for it in frappe.get_all(
		"Item",
		filters={"item_group": ["in", list(allowed)]},
		fields=["name", "modified"],
	):
		current = str(it.modified)
		if it.name not in fib or fib.get(it.name) != current:
			enqueue_item(it.name)
			enqueued += 1
	frappe.db.commit()
	return {"checked": len(fib), "enqueued": enqueued}


# --------------------------------------------------------------------------
# Manual / test helpers (kept from the original integration)
# --------------------------------------------------------------------------


@frappe.whitelist()
def sync_items(limit=5):
	"""Manual / test sync: push the ``limit`` most recently modified items.

	Call as: /api/method/sc_custom.fibery_sync.sync.sync_items
	or in console: frappe.call("sc_custom.fibery_sync.sync.sync_items")
	"""
	host, token, space, database = _get_conf()
	limit = int(limit)

	allowed = _allowed_item_groups()
	items = frappe.get_all(
		"Item",
		filters={"item_group": ["in", list(allowed)]} if allowed else None,
		fields=["item_code", "item_name", "modified", "description",
		        "valuation_rate", "has_serial_no", "has_batch_no",
		        "item_group"],
		order_by="modified desc",
		limit_page_length=limit,
	)
	if not items:
		return {"status": "ok", "items_sent": 0, "message": "No items found"}

	commands = _build_upsert_command(items, space, database)
	status, body = _post_to_fibery(host, token, commands)

	return {
		"status": status,
		"fibery_response": body,
		"items_sent": len(items),
	}


def scheduled_sync():
	"""DEPRECATED — replaced by the outbox (flush_queue) + reconcile().

	Kept only for backward reference; no longer wired into scheduler_events.
	Do not use for new work.
	"""
	host, token, space, database = _get_conf()

	last_sync = frappe.db.get_default("fibery_last_sync") or "1970-01-01 00:00:00"
	items = frappe.get_all(
		"Item",
		filters={"modified": [">", last_sync]},
		fields=["item_code", "item_name", "modified", "description",
		        "valuation_rate", "has_serial_no", "has_batch_no",
		        "item_group"],
		limit_page_length=500,
	)
	if not items:
		return

	commands = _build_upsert_command(items, space, database)
	status, body = _post_to_fibery(host, token, commands)

	if status == 200:
		frappe.db.set_default("fibery_last_sync", now())
		frappe.db.commit()
	else:
		frappe.log_error(
			message=f"Fibery sync failed: HTTP {status}\n{body}",
			title="Fibery Sync",
		)
