// Restore v15-style optimistic comment rendering.
//
// In v16 the comment box no longer draws the new comment after saving; the comment
// appears only when the realtime `docinfo_update` event round-trips back to the browser
// over socketio (see apps/frappe footer.js + form.js setup_docinfo_change_listener).
// When that single event is missed (room-join race, redis pub/sub lag, a momentarily
// dropped websocket) the comment is saved but never rendered until a page refresh.
// This is intermittent and was most visible on drafts / pending docs.
//
// This patches standard Frappe behaviour at the prototype level (we must not edit
// apps/frappe). Re-verify against apps/frappe footer.js / form.js / controls/comment.js
// on each Frappe upgrade.

frappe.provide("frappe.ui.form");

(function () {
	// Insert / replace a docinfo entry without duplicating it (dedupe by name).
	function upsert_docinfo(doctype, docname, key, doc) {
		let info = (frappe.model.docinfo[doctype] || {})[docname];
		if (!info) {
			return;
		}
		if (!Array.isArray(info[key])) {
			info[key] = [];
		}
		let idx = info[key].findIndex((d) => d.name === doc.name);
		if (idx === -1) {
			info[key].push(doc);
		} else {
			info[key].splice(idx, 1, doc);
		}
	}

	// 1) Make the realtime docinfo_update "add" branch idempotent so the optimistic
	//    push below and a later realtime push of the same comment don't double-render.
	frappe.ui.form.Form.prototype.setup_docinfo_change_listener = function () {
		let doctype = this.doctype;
		let docname = this.docname;

		if (this.doc && !this.is_new()) {
			frappe.realtime.doc_subscribe(doctype, docname);
		}
		frappe.realtime.off("docinfo_update");
		frappe.realtime.on("docinfo_update", ({ doc, key, action = "update" }) => {
			if (
				!doc.reference_doctype ||
				!doc.reference_name ||
				doc.reference_doctype !== doctype ||
				doc.reference_name !== docname
			) {
				return;
			}
			let info = (frappe.model.docinfo[doctype] || {})[docname];
			if (!info) {
				return;
			}
			if (!Array.isArray(info[key])) {
				info[key] = [];
			}
			let doc_list = info[key];
			let docindex = doc_list.findIndex((old_doc) => old_doc.name === doc.name);

			// Track whether docinfo actually changed, so we can skip a redundant full
			// timeline rebuild. The realtime echo of a comment we already rendered
			// optimistically would otherwise tear down and re-render the whole timeline
			// a second time (see base_timeline render_timeline_items — it empties and
			// rebuilds every item), causing extra work and flicker on long timelines.
			let changed = false;
			if (action === "add") {
				// dedupe: only push if not already present (optimistic render may have added it)
				if (docindex === -1) {
					doc_list.push(doc);
					changed = true;
				}
			} else if (docindex > -1) {
				if (action === "update") {
					doc_list.splice(docindex, 1, doc);
					changed = true;
				}
				if (action === "delete") {
					doc_list.splice(docindex, 1);
					changed = true;
				}
			}

			if (!changed) {
				return;
			}

			this.timeline && this.timeline.refresh();

			if (["add", "delete"].includes(action) && doc.doctype === "Comment") {
				this.footer.refresh_comments_count();
			}
		});
	};

	// 2) Optimistic render: after add_comment resolves, draw the returned comment
	//    immediately instead of waiting for the realtime event.
	let orig_make_comment_box = frappe.ui.form.Footer.prototype.make_comment_box;
	frappe.ui.form.Footer.prototype.make_comment_box = function () {
		orig_make_comment_box.call(this);

		let footer = this;
		let frm = this.frm;

		// on_submit is stored on the control instance and called as
		// `this.on_submit(this.get_value())` (controls/comment.js) — replace just that.
		frm.comment_box.on_submit = function (comment) {
			if (strip_html(comment).trim() != "" || comment.includes("img")) {
				frm.comment_box.disable();
				frappe
					.xcall("frappe.desk.form.utils.add_comment", {
						reference_doctype: frm.doctype,
						reference_name: frm.docname,
						content: comment,
						comment_email: frappe.session.user,
						comment_by: frappe.session.user_fullname,
					})
					.then((comment_doc) => {
						frm.comment_box.set_value("");
						frappe.utils.play_sound("click");
						if (comment_doc && comment_doc.name) {
							upsert_docinfo(frm.doctype, frm.docname, "comments", comment_doc);
							frm.timeline && frm.timeline.refresh();
							footer.refresh_comments_count();
						}
					})
					.finally(() => {
						frm.comment_box.enable();
					});
			}
		};
	};
})();
