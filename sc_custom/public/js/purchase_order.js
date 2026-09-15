frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        // "Download Item Attachments": pack the .pdf/.stp/.step files attached to the
        // Item master of every row into one ZIP (see sc_custom/api/po_attachments.py)
        if (!frm.is_new() && (frm.doc.items || []).length) {
            frm.add_custom_button(__('Download Item Attachments'), function() {
                sc_custom.download_item_attachments(frm);
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

sc_custom.download_item_attachments = async function(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__('Please save the Purchase Order first.'));
        return;
    }
    const po_name = frm.doc.name;

    // 1. Ask the server what there is to download (no file content yet)
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
            message: __('No .pdf, .stp or .step attachments found on the items of this Purchase Order.'),
            indicator: 'orange',
        });
        return;
    }

    const filename = `${po_name}-item-attachments.zip`;

    // 2. Chrome/Edge: let the user choose where to save first (File System Access API).
    //    Firefox/Safari have no such API and fall through to a regular download.
    let handle = null;
    if (typeof window.showSaveFilePicker === 'function') {
        try {
            handle = await window.showSaveFilePicker({
                suggestedName: filename,
                types: [{ description: 'ZIP archive', accept: { 'application/zip': ['.zip'] } }],
            });
        } catch (e) {
            if (e && e.name === 'AbortError') {
                return; // user cancelled the dialog
            }
            handle = null; // dialog unavailable (e.g. gesture expired) -> regular download
        }
    }

    // 3. Fetch the ZIP and hand it over
    frappe.dom.freeze(__('Preparing ZIP...'));
    try {
        const blob = await sc_custom.fetch_item_attachments_zip(po_name);
        if (handle) {
            const writable = await handle.createWritable();
            await writable.write(blob);
            await writable.close();
        } else {
            sc_custom.save_blob(blob, filename);
        }
    } catch (e) {
        frappe.msgprint({
            title: __('Download failed'),
            message: (e && e.message) || String(e),
            indicator: 'red',
        });
        return;
    } finally {
        frappe.dom.unfreeze();
    }

    frappe.show_alert({
        message: __('Saved {0} files from {1} items, {2} items without attachments skipped', [
            summary.total_files,
            summary.items_with_files,
            summary.items_without_files,
        ]),
        indicator: 'green',
    }, 8);
};

sc_custom.fetch_item_attachments_zip = async function(po_name) {
    const response = await fetch('/api/method/sc_custom.api.po_attachments.download_item_attachments', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
            'Content-Type': 'application/json',
            'X-Frappe-CSRF-Token': frappe.csrf_token,
        },
        body: JSON.stringify({ purchase_order: po_name }),
    });

    if (!response.ok) {
        let message = __('Server returned status {0}', [response.status]);
        try {
            const data = await response.json();
            const server_messages = data._server_messages ? JSON.parse(data._server_messages) : [];
            if (server_messages.length) {
                message = JSON.parse(server_messages[0]).message || message;
            } else if (data.exception) {
                message = data.exception;
            }
        } catch (ignore) {
            // body was not JSON; keep the status message
        }
        throw new Error(message);
    }
    return await response.blob();
};

sc_custom.save_blob = function(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 10000);
};
