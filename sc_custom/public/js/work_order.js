/**
 * Work Order customizations for SC Custom
 *
 * - Default storage fetched from Manufacturing Settings on new docs
 */

frappe.ui.form.on('Work Order', {
	onload: function(frm) {
		if (frm.doc.__islocal) {
			sc_custom_set_default_storage(frm);
		}
	}
});

function sc_custom_set_default_storage(frm) {
	if (!frm.doc.wip_storage && !frm.doc.fg_storage) {
		frappe.call({
			method: "sc_custom.api.queries.get_default_storage",
			callback: function(r) {
				if (r.message) {
					if (r.message.wip_storage) {
						frm.set_value("wip_storage", r.message.wip_storage);
					}
					if (r.message.fg_storage) {
						frm.set_value("fg_storage", r.message.fg_storage);
					}
				}
			}
		});
	}
}
