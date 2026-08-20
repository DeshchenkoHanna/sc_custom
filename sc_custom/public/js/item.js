frappe.ui.form.on("Item", {
	refresh(frm) {
		// Skip for unsaved items — there is no item_code yet and the tree
		// would call get_used_in_boms without it (e.g. "Go to full window").
		if (frm.is_new() || !frm.doc.item_code) return;
		if (!frm.doc.include_item_in_manufacturing) return;

		sc_custom.bom_tree.setup(frm);
	},
});

frappe.ui.form.on("Item", {
	validate(frm) {
		sc_custom.item_form.sync_default_supplier(frm);
	},
	after_save(frm) {
		frm.__empty_default_supplier_confirmed = false;
	},
});

frappe.provide("sc_custom.item_form");

// Default Supplier must always mirror the first Item Supplier row.
// With no supplier rows, an empty Default Supplier needs explicit confirmation.
sc_custom.item_form.sync_default_supplier = function (frm) {
	const first = (frm.doc.supplier_items || [])[0];
	const supplier = first && first.supplier;

	if (supplier) {
		let changed = false;
		if ((frm.doc.item_defaults || []).length) {
			frm.doc.item_defaults.forEach((d) => {
				if (d.default_supplier !== supplier) {
					d.default_supplier = supplier;
					changed = true;
				}
			});
		} else {
			frm.add_child("item_defaults", {
				company: frappe.defaults.get_default("company"),
				default_supplier: supplier,
			});
			changed = true;
		}
		if (changed) {
			frm.refresh_field("item_defaults");
			frappe.show_alert({
				message: __("Default Supplier adapted to the first row of Item Supplier."),
				indicator: "orange",
			});
		}
		return;
	}

	const has_empty_default =
		!(frm.doc.item_defaults || []).length ||
		frm.doc.item_defaults.some((d) => !d.default_supplier);

	// Only purchase items need a default supplier
	if (!cint(frm.doc.is_purchase_item)) return;

	if (has_empty_default && !frm.__empty_default_supplier_confirmed) {
		frappe.validated = false;
		const d = frappe.warn(
			__("No Default Supplier"),
			`<p>${__(
				"A Default Supplier should be selected in the Item Defaults table (Accounting tab)."
			)}</p>
			<p>${__("Are you sure you want to save without a Default Supplier?")}</p>`,
			() => {
				frm.__empty_default_supplier_confirmed = true;
				frm.save();
			},
			__("Yes")
		);
		d.set_secondary_action_label(__("No"));
	}
};

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

		if (!$wrapper.find(".used-in-bom-layout").length) {
			$wrapper.empty();
			$wrapper.append(`
				<style>
					.used-in-bom-layout {
						display: flex;
						gap: 24px;
						padding-top: 10px;
						align-items: stretch;
					}
					.used-in-bom-tree {
						flex: 1 1 50%;
						min-width: 0;
					}
					.used-in-bom-components {
						flex: 1 1 50%;
						min-width: 0;
						border-left: 1px solid var(--border-color);
						padding-left: 16px;
					}
					.used-in-bom-components .placeholder {
						color: var(--text-muted);
						font-style: italic;
					}
					.used-in-bom-components .components-title {
						font-weight: 600;
						margin-bottom: 8px;
					}
					.used-in-bom-components table tr.current-item {
						background: var(--gray-100);
					}
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
					.used-in-bom-tree .tree-separator {
						list-style: none;
						border-top: 1px solid var(--border-color);
						margin: 4px 0 4px 32px;
					}
					.used-in-bom-tree .tree-link > .node-parent,
					.used-in-bom-tree .tree-link > span:first-child {
						align-self: center;
						margin-right: 4px;
					}
					.used-in-bom-tree .tree-link > span:first-child:not(.node-parent) {
						font-family: var(--font-family-monospace, monospace);
						color: var(--text-muted);
					}
				</style>
				<div class="used-in-bom-layout">
					<div class="used-in-bom-tree"></div>
					<div class="used-in-bom-components">
						<div class="placeholder">${__("Click a BOM's components icon to see its content here.")}</div>
					</div>
				</div>
			`);

		}

		// Re-render tree only when item_code changes (form is reused across docs)
		const $layout = $wrapper.find(".used-in-bom-layout");
		if ($layout.data("rendered-for") === frm.doc.item_code) return;
		$layout.data("rendered-for", frm.doc.item_code);

		const $tree_area = $wrapper.find(".used-in-bom-tree");
		const $panel = $wrapper.find(".used-in-bom-components");
		$panel.html(
			`<div class="placeholder">${__("Click a BOM's components icon to see its content here.")}</div>`
		);
		sc_custom.bom_tree.render_tree(frm, $tree_area);
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
			icon_set: {
				open: frappe.utils.icon("folder-open", "md"),
				closed: frappe.utils.icon("folder-normal", "md"),
				leaf: "├─",
			},
			on_node_render(node) {
				if (!node.$ul) return;
				const $children = node.$ul.children("li.tree-node");
				$children.each(function () {
					const $leaf = $(this).find(".tree-link > .node-leaf");
					if (!$leaf.length) return; // expandable nodes keep folder icon
					const $next = $(this).next();
					const is_last_in_group =
						!$next.length || $next.hasClass("tree-separator");
					$leaf.text(is_last_in_group ? "└─" : "├─");
				});
			},
			on_click(node) {
				if (node.is_root) return;
				const bom = node.data && node.data.value;
				if (!bom) return;
				const $panel = $tree_area
					.closest(".used-in-bom-layout")
					.find(".used-in-bom-components");
				// Only auto-update if components panel is already showing some BOM
				if (!$panel.find(".components-title").length) return;
				const $parent_container = node.$tree_link
					.parent()
					.parent()
					.parent();
				const child_label = $parent_container
					.children(".tree-link")
					.first()
					.attr("data-label") || null;
				sc_custom.bom_tree.render_components(
					$panel,
					bom,
					frm.doc.item_code,
					child_label
				);
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

				// Insert separator if previous sibling node has different item_code
				const $li = node.$tree_link.parent();
				const $prev_li = $li.prev("li.tree-node");
				if ($prev_li.length) {
					const prev_node = $prev_li.find("> .tree-link").data("node");
					if (
						prev_node &&
						prev_node.data &&
						prev_node.data.item_code !== node.data.item_code
					) {
						$('<li class="tree-separator"></li>').insertBefore($li);
					}
				}

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
						// Mark this tree-link as selected/active (same as clicking the row)
						frappe.dom.activate(tree.wrapper, node.$tree_link, "tree-link");
						tree.set_selected_node(node);
						tree.wrapper.find(".tree-link.selected").removeClass("selected");
						node.$tree_link.addClass("selected");
						// tree-child = parent node's tree-link in DOM (one step closer to root)
						const $parent_container = node.$tree_link
							.parent()
							.parent()
							.parent();
						const child_label = $parent_container
							.children(".tree-link")
							.first()
							.attr("data-label") || null;
						const $panel = $tree_area
							.closest(".used-in-bom-layout")
							.find(".used-in-bom-components");
						sc_custom.bom_tree.render_components(
							$panel,
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
			// Update leaf connectors: only the very last tree-node gets └─
			// tree.js renders leaf icon as bare <span>, expandable as <span class="node-parent">
			if (node.$ul) {
				node.$ul.children("li.tree-node").each(function () {
					const $first_span = $(this)
						.children(".tree-link")
						.children("span")
						.first();
					if (!$first_span.length || $first_span.hasClass("node-parent")) return;
					const is_last = !$(this).next().length;
					$first_span.text(is_last ? "└─" : "├─");
				});
			}
		};
	},

	render_components($panel, bom_name, current_item_code, tree_child_label) {
		const esc = frappe.utils.escape_html;
		$panel.html(
			`<div class="components-title">${__("Components of {0}", [
				`<a href="/app/bom/${encodeURIComponent(bom_name)}" target="_blank">${esc(bom_name)}</a>`,
			])}</div><div class="text-muted">${__("Loading...")}</div>`
		);

		frappe.call({
			method: "sc_custom.api.bom_tree.get_bom_components",
			args: { bom_name },
			callback: (r) => {
				const rows = r.message || [];
				const title_html = `<div class="components-title">${__("Components of {0}", [
					`<a href="/app/bom/${encodeURIComponent(bom_name)}" target="_blank">${esc(bom_name)}</a>`,
				])}</div>`;

				if (!rows.length) {
					$panel.html(
						title_html +
							`<p class="text-muted">${__("No components found.")}</p>`
					);
					return;
				}

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

				$panel.html(
					title_html +
						`<table class="table table-sm bom-components-table">
							<thead>
								<tr>
									<th>${__("Item")}</th>
									<th class="text-right">${__("Qty")}</th>
									<th>${__("UOM")}</th>
									<th>${__("Sub-BOM")}</th>
								</tr>
							</thead>
							<tbody>${trs}</tbody>
						</table>`
				);
			},
		});
	},
});
