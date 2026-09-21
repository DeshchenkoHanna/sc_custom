frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        // "Create Item Attachments": pack the drawing/CAD files attached to the Item master
        // of every row into one archive (ZIP or 7z) and attach it to the PO as a private file
        // (see sc_custom/api/po_attachments.py)
        const closed_or_cancelled = frm.doc.docstatus === 2 || frm.doc.status === 'Closed';
        if (!frm.is_new() && (frm.doc.items || []).length && !closed_or_cancelled) {
            frm.add_custom_button(__('Create Item Attachments'), function() {
                sc_custom.create_item_attachments(frm);
            });
        }
    },

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

frappe.provide('sc_custom');

sc_custom.create_item_attachments = async function(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__('Please save the Purchase Order first.'));
        return;
    }
    const po_name = frm.doc.name;

    // 1. Ask the server what there is to pack and whether an archive already exists
    let summary;
    try {
        const r = await frappe.call({
            method: 'sc_custom.api.po_attachments.get_item_attachment_summary',
            args: { purchase_order: po_name },
            freeze: true,
            freeze_message: __('Checking item attachments...'),
        });
        summary = r.message || {};
    } catch (e) {
        return; // frappe.call already showed the server error
    }

    if (!summary.total_files) {
        frappe.msgprint({
            title: __('No attachments'),
            message: __('No matching attachments (.pdf, .stp, .step, .frl3, .vikx, .dxf) found on the items of this Purchase Order.'),
            indicator: 'orange',
        });
        return;
    }

    // 2. Format choice (+ replace warning when an archive already exists)
    const fields = [
        {
            fieldname: 'archive_format',
            fieldtype: 'Select',
            label: __('Format'),
            options: [
                { value: 'zip', label: 'ZIP' },
                { value: '7z', label: '7z' },
            ],
            default: 'zip',
            reqd: 1,
            description: __('{0} files from {1} items will be packed.', [summary.total_files, summary.items_with_files]),
        },
    ];
    if (summary.existing_archive) {
        fields.push({
            fieldname: 'replace_warning',
            fieldtype: 'HTML',
            options: `<div class="alert alert-warning" style="margin-top: 8px;">${frappe.utils.escape_html(
                __('Are you sure you want to create a new attachment? It will replace the existing attached file {0}.', [summary.existing_archive])
            )}</div>`,
        });
    }

    const dialog = new frappe.ui.Dialog({
        title: __('Create Item Attachments'),
        fields: fields,
        primary_action_label: summary.existing_archive ? __('Replace') : __('Create'),
        primary_action: function(values) {
            dialog.hide();
            sc_custom.run_create_item_attachments(frm, values.archive_format);
        },
    });
    dialog.show();
};

sc_custom.run_create_item_attachments = async function(frm, archive_format) {
    let result;
    try {
        const r = await frappe.call({
            method: 'sc_custom.api.po_attachments.create_item_attachments',
            args: { purchase_order: frm.doc.name, archive_format: archive_format },
            freeze: true,
            freeze_message: __('Creating archive...'),
        });
        result = r.message || {};
    } catch (e) {
        return; // server error already shown
    }

    // 3. Reload so the attachments sidebar shows the new file, then report
    await frm.reload_doc();
    frappe.show_alert({
        message: __('{0} attached: {1} files from {2} items, {3} items without attachments skipped', [
            result.file_name,
            result.total_files,
            result.items_with_files,
            result.items_without_files,
        ]),
        indicator: 'green',
    }, 8);
};
