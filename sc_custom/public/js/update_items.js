// "Tools > Update Item Names" on draft Material Request, Request for Quotation,
// Purchase Order and Stock Entry (FEAT-186). Mirrors BOM "Update Cost" (save=false):
// the server computes the changes (sc_custom/api/update_items.py), they are written
// into the open form and the user saves. Each doctype script calls
// sc_custom.update_items.add_button(frm) from its refresh handler.
frappe.provide('sc_custom.update_items');

sc_custom.update_items.add_button = function(frm) {
    if (frm.doc.docstatus !== 0 || !(frm.doc.items || []).length) return;
    frm.add_custom_button(__('Update Item Names'), function() {
        sc_custom.update_items.run(frm);
    }, __('Tools'));
};

sc_custom.update_items.run = function(frm) {
    const items = (frm.doc.items || []).map(row => ({
        name: row.name,
        idx: row.idx,
        item_code: row.item_code,
        item_name: row.item_name,
    }));

    frappe.call({
        method: 'sc_custom.api.update_items.get_item_updates',
        args: { doctype: frm.doc.doctype, items: items },
        freeze: true,
        callback(r) {
            const changes = r.message || [];
            if (!changes.length) {
                frappe.show_alert({ message: __('No changes in item names found'), indicator: 'blue' });
                return;
            }

            const row_doctype = frm.fields_dict.items.grid.doctype;
            changes.forEach(c => frappe.model.set_value(row_doctype, c.name, c.fieldname, c.new));
            frm.refresh_field('items');

            const rows = changes.map(c =>
                `<tr><td>${c.idx}</td><td>${frappe.utils.escape_html(c.item_code)}</td>` +
                `<td>${frappe.utils.escape_html(c.old)}</td><td>${frappe.utils.escape_html(c.new)}</td></tr>`
            ).join('');
            frappe.msgprint({
                title: __('Item Names Updated'),
                indicator: 'green',
                message: `<p>${__('{0} row(s) updated. Save the document to keep the changes.', [changes.length])}</p>` +
                    `<table class="table table-bordered table-sm"><thead><tr>` +
                    `<th>#</th><th>${__('Item Code')}</th><th>${__('Old Name')}</th><th>${__('New Name')}</th>` +
                    `</tr></thead><tbody>${rows}</tbody></table>`,
            });
        },
    });
};
