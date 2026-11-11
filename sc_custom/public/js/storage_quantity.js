// Stock per Storage (inventory dimension) for Item: the "Storage Quantity" table.
//
// Loaded on every desk page (app_include_js) because two surfaces draw it:
//   1. the Item form, HTML field custom_storage_quantity — item.js calls
//      sc_custom.storage_quantity.render(frm) on refresh;
//   2. the report side panel (Frappe >= 16.50, frappe.ui.SidePanel): a frm-less
//      preview that deliberately skips HTML fields and runs no form scripts, so
//      the table is appended here under Default Unit of Measure (see the patch
//      at the end of this file).
// Data: sc_custom.api.item_storage.get_stock_by_storage_for_item
// (rows: storage_location, actual_qty).
//
// PATCHED CORE SYMBOLS (JS prototype patch — re-verify on every Frappe upgrade):
//   frappe.ui.SidePanel.prototype.render_doc  (frappe/public/js/frappe/ui/side_panel.js,
//     side_panel.bundle.js, loaded lazily on the first Link click in a report)
//   Relies on: this.layouts[doctype] = { layout, $wrapper }, layout.fields_dict.stock_uom.$wrapper,
//   this.current = { doctype, docname }.

frappe.provide("sc_custom.storage_quantity");

// Plain-number quantity (no thousands separator, no trailing zeros). Global on purpose:
// item.js (BOM tree) runs inside Frappe's form-script scope and reuses it.
function format_qty(value) {
	if (value == null) return "";
	const f = parseFloat(value);
	if (isNaN(f)) return "";
	return Number.isInteger(f) ? String(f) : f.toString();
}

$.extend(sc_custom.storage_quantity, {
	render(frm) {
		const field = frm.fields_dict.custom_storage_quantity;
		if (!field) return;
		const $wrapper = field.$wrapper;
		$wrapper.html(`<div class="text-muted">${__("Loading quantities...")}</div>`);

		const item_code = frm.doc.name;
		frappe.call({
			method: "sc_custom.api.item_storage.get_stock_by_storage_for_item",
			args: { item_code },
			callback(r) {
				// the user may have moved to another Item while the call was in flight
				if (frm.doc.name !== item_code) return;
				$wrapper.html(sc_custom.storage_quantity.html(r.message || [], frm.doc.stock_uom));
			},
		});
	},

	html(rows, uom) {
		const box_css = `.sc-storage-qty-box { width: 100%; margin-bottom: var(--margin-md); border: 1px solid var(--table-border-color, var(--border-color));
					border-radius: var(--radius-md); background-color: var(--subtle-accent); overflow: hidden; }`;
		if (!rows.length) {
			// Same look as Frappe's empty child-table grid (.form-grid + .grid-empty): framed box, centred muted text.
			return `
				<style>
					${box_css}
					.sc-storage-qty-empty { min-height: 75px; display: flex; align-items: center; justify-content: center;
						padding: var(--padding-lg) var(--padding-sm); color: var(--text-muted); font-size: var(--text-sm); }
				</style>
				<div class="sc-storage-qty-box"><div class="sc-storage-qty-empty">${__("No stock found for any storage location.")}</div></div>`;
		}
		const total = rows.reduce((sum, row) => sum + (parseFloat(row.actual_qty) || 0), 0);
		const body = rows
			.map(
				(row) => `<tr>
					<td>${frappe.utils.escape_html(row.storage_location || __("Not Specified"))}</td>
					<td class="sc-qty">${format_qty(row.actual_qty)}</td>
				</tr>`
			)
			.join("");
		// Same recipe as Frappe's own child-table grid (frappe/public/scss/common/grid.scss):
		// outer border + radius-md on the box, muted heading on subtle-fg, rows on fg-color.
		return `
			<style>
				${box_css}
				.sc-storage-qty { width: 100%; border-collapse: collapse; font-size: var(--text-sm); font-weight: var(--font-weight-regular, 400); letter-spacing: 0.02em; }  /* Frappe grid text style "sm" (grid.scss get_textstyle) */
				.sc-storage-qty th { padding: 0 8px; height: 32px; font-weight: var(--font-weight-regular, 400); text-align: left; color: var(--text-muted);
					background-color: var(--subtle-fg); border-bottom: 1px solid var(--table-border-color, var(--border-color)); }
				.sc-storage-qty td { padding: 6px 8px; background-color: var(--fg-color); border-bottom: 1px solid var(--table-border-color, var(--border-color)); }
				.sc-storage-qty th + th, .sc-storage-qty td + td { border-left: 1px solid var(--table-border-color, var(--border-color)); }
				.sc-storage-qty tr:last-child td { border-bottom: none; }
				.sc-storage-qty .sc-qty { text-align: right; white-space: nowrap; width: 30%; }
				.sc-storage-qty tfoot th { border-bottom: none; border-top: 1px solid var(--table-border-color, var(--border-color)); }  /* Total row = same style as heading */
			</style>
			<div class="sc-storage-qty-box">
			<table class="sc-storage-qty">
				<thead><tr><th>${__("Storage Location")}</th><th class="sc-qty">${__("Actual Quantity")} (${frappe.utils.escape_html(uom || "")})</th></tr></thead>
				<tbody>${body}</tbody>
				${rows.length > 1 ? `<tfoot><tr><th>${__("Total")}</th><th class="sc-qty">${format_qty(total)}</th></tr></tfoot>` : ""}
			</table>
			</div>`;
	},
});

// ---------------------------------------------------------------------------
// Report side panel: show the same table when an Item is previewed there.
// ---------------------------------------------------------------------------
(function () {
	const CONTAINER_CLASS = "sc-side-panel-storage";

	function render_in_panel(panel, doctype) {
		if (doctype !== "Item") return;
		const entry = panel.layouts && panel.layouts[doctype];
		const anchor = entry && entry.layout.fields_dict.stock_uom;
		if (!anchor || !anchor.$wrapper) return;

		let $box = entry.$wrapper.find("." + CONTAINER_CLASS);
		if (!$box.length) {
			$box = $(`<div class="${CONTAINER_CLASS} frappe-control"></div>`);
			anchor.$wrapper.after($box);
		}
		const doc = entry.layout.doc || {};
		const item_code = doc.name;
		if (!item_code) {
			$box.empty();
			return;
		}
		$box.html(`<div class="text-muted">${__("Loading quantities...")}</div>`);
		frappe.call({
			method: "sc_custom.api.item_storage.get_stock_by_storage_for_item",
			args: { item_code },
			callback(r) {
				// the panel may already show another document
				if (!panel.current || panel.current.docname !== item_code) return;
				$box.html(sc_custom.storage_quantity.html(r.message || [], doc.stock_uom));
			},
		});
	}

	function patch_side_panel(SidePanel) {
		if (!SidePanel || !SidePanel.prototype || SidePanel.prototype.__sc_storage_patched) return;
		const original = SidePanel.prototype.render_doc;
		if (typeof original !== "function") return;
		SidePanel.prototype.render_doc = function (doctype, preview) {
			const result = original.apply(this, arguments);
			try {
				render_in_panel(this, doctype);
			} catch (e) {
				console.error("sc_custom storage_quantity: side panel render failed", e);
			}
			return result;
		};
		SidePanel.prototype.__sc_storage_patched = true;
	}

	// side_panel.bundle.js is loaded lazily, after this file. Catch the class at the moment
	// the bundle assigns frappe.ui.SidePanel; if it is somehow there already, patch now.
	frappe.provide("frappe.ui");
	if (frappe.ui.SidePanel) {
		patch_side_panel(frappe.ui.SidePanel);
	} else {
		let klass;
		Object.defineProperty(frappe.ui, "SidePanel", {
			configurable: true,
			enumerable: true,
			get() {
				return klass;
			},
			set(value) {
				klass = value;
				patch_side_panel(value);
			},
		});
	}
})();
