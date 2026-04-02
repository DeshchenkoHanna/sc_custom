frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.doc.include_item_in_manufacturing) return;

		sc_custom.bom_tree.setup(frm);
	},
});

frappe.provide("sc_custom.bom_tree");

$.extend(sc_custom.bom_tree, {
	setup(frm) {
		const wrapper = frm.fields_dict.custom_used_in_boms_html;
		if (!wrapper) return;

		const $wrapper = wrapper.$wrapper;

		// Build controls + tree area once
		if (!$wrapper.find(".used-in-bom-tree").length) {
			$wrapper.empty();
			$wrapper.append(`
				<div style="position: relative;">
					<div style="position: absolute; top: 0; right: 10px; z-index: 1;">
						<div class="form-check mb-2">
							<input class="form-check-input include-sub-assemblies-check"
								type="checkbox">
							<label class="form-check-label">
								${__("Including sub-assemblies")}
							</label>
						</div>
						<div class="form-check">
							<input class="form-check-input do-not-explode-check"
								type="checkbox" checked>
							<label class="form-check-label">
								${__("Do Not Explode")}
							</label>
						</div>
					</div>
					<div class="used-in-bom-tree" style="padding-top: 10px;"></div>
				</div>
			`);

			const $tree_area = $wrapper.find(".used-in-bom-tree");

			const reload_tree = () => {
				const include_sub = $wrapper.find(".include-sub-assemblies-check").is(":checked") ? 1 : 0;
				const do_not_explode = $wrapper.find(".do-not-explode-check").is(":checked") ? 1 : 0;
				sc_custom.bom_tree.render_tree(frm, $tree_area, include_sub, do_not_explode);
			};

			$wrapper.find(".include-sub-assemblies-check").on("change", reload_tree);
			$wrapper.find(".do-not-explode-check").on("change", reload_tree);

			reload_tree();
		}
	},

	render_tree(frm, $tree_area, include_sub_assemblies, do_not_explode) {
		$tree_area.empty();

		new frappe.ui.Tree({
			parent: $tree_area,
			label: frm.doc.item_code,
			root_value: frm.doc.item_code,
			expandable: true,
			with_skeleton: 0,
			method: "sc_custom.api.bom_tree.get_used_in_boms",
			args: {
				item_code: frm.doc.item_code,
				include_sub_assemblies: include_sub_assemblies,
				do_not_explode: do_not_explode,
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
					label += `<a href="/app/item/${encodeURIComponent(item_code)}" class="bom-tree-link" target="_blank">${esc(item_code)}</a>`;
					if (item_name && item_name !== item_code) {
						label += `: ${esc(item_name)}`;
					}
				}
				if (node.data.qty) {
					label += ` <span class="badge badge-pill badge-light">${node.data.qty} ${esc(
						__(node.data.stock_uom)
					)}</span>`;
				}
				return label;
			},
		});
	},
});
