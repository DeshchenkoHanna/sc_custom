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
    fibery_host             e.g. "youraccount.fibery.io"   (no scheme)
    fibery_token            Fibery API token
    fibery_space            optional, default "ERP Dev"
    fibery_db               optional, default "Test-Items"
    fibery_item_code_field  optional, default "Item Code"
                            (Text field that MUST exist in the Fibery DB;
                            used as both the per-entity key and the
                            conflict-field for the upsert. Renaming this
                            does not break existing entities because they
                            are matched by deterministic fibery/id.)
    fibery_modified_field   optional, default "ERP Modified"
                            (Text field that MUST exist in the Fibery DB;
                            used by reconcile() to detect drift)
    fibery_description_field  optional, default "Item Description"
                            (plain Text field that MUST exist in the Fibery
                            DB; Item.description is HTML and is stripped to
                            plain text before sending. NOTE: this is NOT the
                            built-in rich-text "Description" field.)
"""

import json
import uuid

import requests

import frappe
from frappe.utils import now

FIBERY_TIMEOUT = 60
OUTBOX = "Fibery Sync Queue"

# Stable namespace so a given item_code always maps to the same fibery/id.
# Fibery requires fibery/id on every entity in a create-or-update batch;
# a deterministic id keeps re-runs idempotent.
_FIBERY_ID_NAMESPACE = uuid.UUID("6f1e7c2a-4b3d-5e6f-8a9b-0c1d2e3f4a5b")


def _fibery_id(item_code):
	"""Deterministic UUID for an item_code (uuid5 over a fixed namespace)."""
	return str(uuid.uuid5(_FIBERY_ID_NAMESPACE, item_code))


def _get_conf():
	"""Read and validate Fibery connection settings from site_config.json.

	Returns (host, token, space, database, code_field, mod_field, desc_field).
	Raises (frappe.throw) if host/token are missing.
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
	database = frappe.conf.get("fibery_db") or "Test-Items"
	code_field = frappe.conf.get("fibery_item_code_field") or "Item Code"
	mod_field = frappe.conf.get("fibery_modified_field") or "ERP Modified"
	desc_field = frappe.conf.get("fibery_description_field") or "Item Description"
	return host, token, space, database, code_field, mod_field, desc_field


def _plain_text(html):
	"""Item.description is a Text Editor (HTML); Fibery target is plain text."""
	if not html:
		return ""
	return frappe.utils.strip_html_tags(html).strip()


def _build_upsert_command(items, space, database, code_field, mod_field, desc_field):
	"""Build the Fibery create-or-update batch command for the given items.

	Uses ``fibery.entity.batch/create-or-update`` with ``code_field`` as the
	conflict field and ``update-latest`` so existing rows are refreshed
	rather than duplicated. Also sends the ERP ``modified`` timestamp into
	``mod_field`` (drift detection) and the HTML-stripped Item description
	into ``desc_field``.
	"""
	return [
		{
			"command": "fibery.entity.batch/create-or-update",
			"args": {
				"type": f"{space}/{database}",
				"entities": [
					{
						"fibery/id": _fibery_id(i.item_code),
						f"{space}/{code_field}": i.item_code,
						f"{space}/Name": i.item_name,
						f"{space}/{mod_field}": str(i.modified),
						f"{space}/{desc_field}": _plain_text(i.description),
					}
					for i in items
				],
				"conflict-field": f"{space}/{code_field}",
				"conflict-action": "update-latest",
			},
		}
	]


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
	"""Fibery returns HTTP 200 with [{"success": true, ...}] on success."""
	return (
		status == 200
		and isinstance(body, list)
		and body
		and body[0].get("success") is True
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

	if isinstance(body, list) and body and body[0].get("success") is False:
		# Fibery nests the error under result: {name, message, ...}
		res = body[0].get("result")
		res = res if isinstance(res, dict) else {}
		name = res.get("name") or body[0].get("name") or "unknown"
		msg = res.get("message") or str(body[0])
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
	"""
	if not item_code:
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
	"""Idempotent seeder: enqueue every Item. Run once after install."""
	count = 0
	for item_code in frappe.get_all("Item", pluck="name"):
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
		host, token, space, database, code_field, mod_field, desc_field = _get_conf()
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
				["item_code", "item_name", "modified", "description"],
				as_dict=True,
			)
			if not item:
				# Item deleted after enqueue — drop the work item.
				frappe.delete_doc(OUTBOX, name, ignore_permissions=True, force=True)
				frappe.db.commit()
				continue

			commands = _build_upsert_command(
				[item], space, database, code_field, mod_field, desc_field
			)
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


def _fibery_snapshot(host, token, space, database, code_field, mod_field):
	"""Page through Fibery and return {item_code: modified_str}."""
	snapshot = {}
	offset = 0
	page = 1000
	ic_field = f"{space}/{code_field}"
	md_field = f"{space}/{mod_field}"
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
	drainer delivers."""
	host, token, space, database, code_field, mod_field, _desc_field = _get_conf()

	fib = _fibery_snapshot(host, token, space, database, code_field, mod_field)
	if fib is None:
		return  # Fibery unreachable/misconfigured; logged in snapshot.

	enqueued = 0
	for it in frappe.get_all("Item", fields=["name", "modified"]):
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
	host, token, space, database, code_field, mod_field, desc_field = _get_conf()
	limit = int(limit)

	items = frappe.get_all(
		"Item",
		fields=["item_code", "item_name", "modified", "description"],
		order_by="modified desc",
		limit_page_length=limit,
	)
	if not items:
		return {"status": "ok", "items_sent": 0, "message": "No items found"}

	commands = _build_upsert_command(
		items, space, database, code_field, mod_field, desc_field
	)
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
	host, token, space, database, code_field, mod_field, desc_field = _get_conf()

	last_sync = frappe.db.get_default("fibery_last_sync") or "1970-01-01 00:00:00"
	items = frappe.get_all(
		"Item",
		filters={"modified": [">", last_sync]},
		fields=["item_code", "item_name", "modified", "description"],
		limit_page_length=500,
	)
	if not items:
		return

	commands = _build_upsert_command(
		items, space, database, code_field, mod_field, desc_field
	)
	status, body = _post_to_fibery(host, token, commands)

	if status == 200:
		frappe.db.set_default("fibery_last_sync", now())
		frappe.db.commit()
	else:
		frappe.log_error(
			message=f"Fibery sync failed: HTTP {status}\n{body}",
			title="Fibery Sync",
		)
