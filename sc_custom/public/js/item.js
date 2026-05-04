frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.doc.include_item_in_manufacturing) return;

		sc_custom.bom_tree.setup(frm);
	},
});

frappe.provide("sc_custom.bom_tree");

function format_qty(value) {
	if (value == null) return "";
	const f = parseFloat(value);
	if (isNaN(f)) return "";
	return Number.isInteger(f) ? String(f) : f.toString();
}

$.extend(sc_custom.bom_tree, {
	setup(frm) {
		const wrapper = frm.fields_dict.custom_used_in_boms_html;
		if (!wrapper) return;

		const $wrapper = wrapper.$wrapper;

		if (!$wrapper.find(".used-in-bom-tree").length) {
			$wrapper.empty();
			$wrapper.append(`
				<style>
					.used-in-bom-tree .qty-badge {
						display: inline-block;
						padding: 0 0.6em;
						margin-left: 4px;
						background: var(--gray-100);
						border-radius: 4px;
						font-variant-numeric: tabular-nums;
						line-height: inherit;
					}
					.used-in-bom-tree .bom-components-trigger {
						margin-left: 0px;
						cursor: pointer;
						color: var(--text-muted);
						align-self: center;
					}
					.used-in-bom-tree .bom-components-trigger:hover {
						color: var(--text-color);
					}
				</style>
				<div class="used-in-bom-tree" style="padding-top: 10px;"></div>
			`);

			const $tree_area = $wrapper.find(".used-in-bom-tree");

			$tree_area.on("click", ".bom-components-trigger", function (e) {
				e.preventDefault();
				e.stopPropagation();
				const bom = $(this).data("bom");
				if (bom) sc_custom.bom_tree.show_components(bom, frm.doc.item_code);
			});

			sc_custom.bom_tree.render_tree(frm, $tree_area);
		}
	},

	render_tree(frm, $tree_area) {
		$tree_area.empty();

		const tree = new frappe.ui.Tree({
			parent: $tree_area,
			label: frm.doc.item_code,
			root_value: frm.doc.item_code,
			expandable: true,
			with_skeleton: 0,
			method: "sc_custom.api.bom_tree.get_used_in_boms",
			args: {
				item_code: frm.doc.item_code,
			},
			get_label(node) {
				if (node.is_root) {
					return node.data.value;
				}
				const esc = frappe.utils.escape_html;
				const bom = node.data.value;
				const item_code = node.data.item_code;
				const item_name = node.data.item_name || "";

				let label = "";
				if (bom) {
					label += `<a href="/app/bom/${encodeURIComponent(bom)}" class="bom-tree-link" target="_blank">${esc(bom)}</a>`;
				}
				if (item_code) {
					if (bom) label += ` &nbsp;—&nbsp; `;
					const display = item_name || item_code;
					label += `<a href="/app/item/${encodeURIComponent(item_code)}" class="bom-tree-link" target="_blank">${esc(display)}</a>`;
				}
				if (node.data.qty != null) {
					const qty = format_qty(node.data.qty);
					const uom = esc(__(node.data.stock_uom || ""));
					label += ` <span class="qty-badge" title="${uom}">${qty}</span>`;
				}
				return label;
			},
			on_render(node) {
				if (node.is_root) return;
				const bom = node.data && node.data.value;
				if (!bom) return;
				if (node.$tree_link.find(".bom-components-trigger").length) return;
				const $icon = $(frappe.utils.icon("list-alt", "sm"));
				const $trigger = $('<span class="bom-components-trigger"></span>')
					.attr("data-bom", bom)
					.attr("title", __("Show components"))
					.css({
						"align-self": "center",
						display: "flex",
						"align-items": "center",
					})
					.append($icon)
					.on("click", (e) => {
						e.preventDefault();
						e.stopPropagation();
						// tree-child = parent node's tree-link in DOM (one step closer to root)
						const $parent_container = node.$tree_link
							.parent()
							.parent()
							.parent();
						const child_label = $parent_container
							.children(".tree-link")
							.first()
							.attr("data-label") || null;
						sc_custom.bom_tree.show_components(
							bom,
							frm.doc.item_code,
							child_label
						);
					});
				node.$tree_link.append($trigger);
			},
		});

		// Tree's toggle_node does `find(".icon").parent().html(icon_set.closed)`
		// inside tree-link, which clobbers our trigger SVG. Detach the trigger
		// around the original call and re-attach so it survives expansion clicks.
		const origToggle = tree.toggle_node.bind(tree);
		tree.toggle_node = function (node) {
			const $trigger = node.$tree_link.find(".bom-components-trigger").detach();
			origToggle(node);
			if ($trigger.length) {
				node.$tree_link.append($trigger);
			}
		};
	},

	show_components(bom_name, current_item_code, tree_child_label) {
		frappe.call({
			method: "sc_custom.api.bom_tree.get_bom_components",
			args: { bom_name },
			callback: (r) => {
				const rows = r.message || [];
				const dialog = new frappe.ui.Dialog({
					title: __("Components of {0}", [bom_name]),
					size: "large",
				});
				const esc = frappe.utils.escape_html;
				const $body = $(dialog.body);
				if (!rows.length) {
					$body.html(`<p class="text-muted">${__("No components found.")}</p>`);
				} else {
					const trs = rows
						.map((row) => {
							const qty = format_qty(row.qty);
							const item_code_link = `<a href="/app/item/${encodeURIComponent(row.item_code)}" target="_blank">${esc(row.item_code)}</a>`;
							const item_name = row.item_name && row.item_name !== row.item_code
								? `: ${esc(row.item_name)}`
								: "";
							const bom_link = row.bom_no
								? `<a href="/app/bom/${encodeURIComponent(row.bom_no)}" target="_blank">${esc(row.bom_no)}</a>`
								: "";
							const is_match =
								row.item_code === current_item_code ||
								(tree_child_label && row.bom_no === tree_child_label);
							const row_class = is_match ? ' class="current-item"' : "";
							return `<tr${row_class}>
								<td>${item_code_link}${item_name}</td>
								<td class="text-right">${qty}</td>
								<td>${esc(row.stock_uom || "")}</td>
								<td>${bom_link}</td>
							</tr>`;
						})
						.join("");
					$body.html(`
						<style>
							.bom-components-table tr.current-item { background: var(--gray-100); }
						</style>
						<table class="table table-sm bom-components-table">
							<thead>
								<tr>
									<th>${__("Item")}</th>
									<th class="text-right">${__("Qty")}</th>
									<th>${__("UOM")}</th>
									<th>${__("Sub-BOM")}</th>
								</tr>
							</thead>
							<tbody>${trs}</tbody>
						</table>
					`);
				}
				dialog.show();
			},
		});
	},
});
