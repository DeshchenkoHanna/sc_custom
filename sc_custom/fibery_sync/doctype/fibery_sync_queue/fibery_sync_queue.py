# Copyright (c) 2026, SwissCluster and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FiberySyncQueue(Document):
	pass


def on_doctype_update():
	# Index for the flush_queue() picker (status + backoff window).
	frappe.db.add_index("Fibery Sync Queue", ["status", "last_attempt"])
