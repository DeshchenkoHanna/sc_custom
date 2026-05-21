frappe.ui.form.on("BOM Item", {
	item_code(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (!row.item_code) return;

		frappe.db.get_value("Item", row.item_code, "custom_do_not_explode_default", (r) => {
			if (r && r.custom_do_not_explode_default) {
				frappe.model.set_value(cdt, cdn, "do_not_explode", 1);
			}
		});
	}
});

frappe.ui.form.on("BOM", {
	refresh(frm) {
		if (frm.is_new()) return;
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Work Order",
				filters: {
					bom_no: frm.doc.name,
					custom_bom_comments: ["is", "set"]
				},
				fields: ["name", "custom_bom_comments", "creation", "modified_by"],
				order_by: "creation desc",
				limit_page_length: 0
			},
			callback(r) {
				const $container = frm.get_field("custom_wo_comments").$wrapper;
				const data = r.message;
				let html = "";

				const tableStyle = "width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;border:1px solid var(--table-border-color);border-radius:var(--border-radius-md);overflow:hidden;margin-bottom:var(--margin-md);background-color:var(--fg-color);";
				const thStyle = "padding:6px 8px;text-align:left;background:var(--subtle-fg);border-bottom:1px solid var(--table-border-color);font-weight:var(--text-regular);color:var(--gray-600);font-size:var(--text-sm);";
				const tdStyle = "padding:8px;vertical-align:top;border-bottom:1px solid var(--table-border-color);word-wrap:break-word;white-space:normal;";
				const divider = "border-right:1px solid var(--table-border-color);";

				if (data && data.length > 0) {
					const lastIdx = data.length - 1;
					let rows = data.map((wo, i) => {
						const date = frappe.datetime.str_to_user(wo.creation.split(" ")[0]);
						const link = `<a href="/app/work-order/${wo.name}">${wo.name}</a>`;
						const bottom = i === lastIdx ? "border-bottom:none;" : "";
						return `<tr>
									<td style="${tdStyle}${divider}${bottom}">${link}<br><span class="text-muted">${date}</span></td>
									<td style="${tdStyle}${bottom}">${wo.custom_bom_comments}</td>
								</tr>`;
					}).join("");

					html = `<table style="${tableStyle}">
								<thead>
									<tr>
										<th style="${thStyle}${divider}width:33%;">Work Order</th>
										<th style="${thStyle}">Comment</th>
									</tr>
								</thead>
								<tbody>${rows}</tbody>
							</table>`;
				} else {
					const emptyStyle = "width:100%;border:1px solid var(--table-border-color);border-radius:var(--border-radius-md);background-color:var(--fg-color);margin-bottom:var(--margin-md);padding:24px;text-align:center;color:var(--text-muted);";
					html = `<div style="${emptyStyle}">
								<img src="/assets/frappe/images/ui-states/grid-empty-state.svg"
									 alt="Grid Empty State" class="grid-empty-illustration">
								<div>No comments from Work Orders</div>
							</div>`;
				}

				$container.html(html);
			}
		});
	}
});
