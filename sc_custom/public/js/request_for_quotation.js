frappe.ui.form.on('Request for Quotation', {
    refresh: function(frm) {
        // "Tools > Update Item Names" (FEAT-186), see public/js/update_items.js
        sc_custom.update_items.add_button(frm);
    },
});
