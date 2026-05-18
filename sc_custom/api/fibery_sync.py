"""
Fibery item sync.

Pushes Item Code + Item Name from ERPNext to a Fibery database via the
Fibery Commands API (``fibery.entity/upsert-batch``).

Why this lives in an app method instead of a Server Script:
Frappe Server Scripts run under RestrictedPython, where ``frappe.conf`` is
NOT exposed and ``import requests`` is blocked. App code has no such
restriction, so the Fibery host/token stay in ``site_config.json`` (never
in the DB, never committed) and are read here with ``frappe.conf.get``.

site_config.json keys:
    fibery_host    e.g. "youraccount.fibery.io"   (no scheme)
    fibery_token   Fibery API token

Optional overrides (defaults match the integration plan):
    fibery_space   default "ERP Dev"
    fibery_db      default "Test-Items"
"""

import json
import uuid

import requests

import frappe
from frappe.utils import now

FIBERY_TIMEOUT = 60

# Stable namespace so a given item_code always maps to the same fibery/id.
# Fibery requires fibery/id on every entity in a create-or-update batch;
# a deterministic id keeps re-runs idempotent.
_FIBERY_ID_NAMESPACE = uuid.UUID("6f1e7c2a-4b3d-5e6f-8a9b-0c1d2e3f4a5b")


def _fibery_id(item_code):
	"""Deterministic UUID for an item_code (uuid5 over a fixed namespace)."""
	return str(uuid.uuid5(_FIBERY_ID_NAMESPACE, item_code))


def _get_conf():
	"""Read and validate Fibery connection settings from site_config.json."""
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
	return host, token, space, database


def _build_upsert_command(items, space, database):
	"""Build the Fibery create-or-update batch command for the given items.

	Uses ``fibery.entity.batch/create-or-update`` with Item Code as the
	conflict field and ``update-latest`` so existing rows are refreshed
	rather than duplicated.
	"""
	return [
		{
			"command": "fibery.entity.batch/create-or-update",
			"args": {
				"type": f"{space}/{database}",
				"entities": [
					{
						"fibery/id": _fibery_id(i.item_code),
						f"{space}/Item Code": i.item_code,
						f"{space}/Name": i.item_name,
					}
					for i in items
				],
				"conflict-field": f"{space}/Item Code",
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


@frappe.whitelist()
def sync_items(limit=5):
	"""Manual / test sync: push the ``limit`` most recently modified items.

	Call as: /api/method/sc_custom.api.fibery_sync.sync_items
	or in console: frappe.call("sc_custom.api.fibery_sync.sync_items")
	"""
	host, token, space, database = _get_conf()
	limit = int(limit)

	items = frappe.get_all(
		"Item",
		fields=["item_code", "item_name"],
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
	"""Incremental sync for a Scheduler Event hook.

	Pushes only Items modified since the last successful run. The watermark
	is stored via Frappe defaults (no extra DocType needed).
	"""
	host, token, space, database = _get_conf()

	last_sync = frappe.db.get_default("fibery_last_sync") or "1970-01-01 00:00:00"
	items = frappe.get_all(
		"Item",
		filters={"modified": [">", last_sync]},
		fields=["item_code", "item_name"],
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
