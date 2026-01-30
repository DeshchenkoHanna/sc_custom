frappe.ui.form.on('Purchase Order', {
    custom_expected_delivery_date: function(frm) {
        // When custom_expected_delivery_date changes, update all existing item rows
        if (frm.doc.custom_expected_delivery_date) {
            frm.doc.items.forEach(function(item) {
                frappe.model.set_value(item.doctype, item.name, 'expected_delivery_date', frm.doc.custom_expected_delivery_date);
            });
            frm.refresh_field('items');
        }
    }
});

frappe.ui.form.on('Purchase Order Item', {
    items_add: function(frm, cdt, cdn) {
        // When a new item row is added, set expected_delivery_date from parent's custom_expected_delivery_date
        if (frm.doc.custom_expected_delivery_date) {
            frappe.model.set_value(cdt, cdn, 'expected_delivery_date', frm.doc.custom_expected_delivery_date);
        }
    }
});
