// Clear the standard make_dashboard handler(s) registered by erpnext batch.js.
// Frappe accumulates handlers in arrays — without this, both standard and custom run.
if (frappe.ui.form.handlers["Batch"]) {
    frappe.ui.form.handlers["Batch"]["make_dashboard"] = [];
}

frappe.ui.form.on("Batch", {
    make_dashboard: function (frm) {
        if (frm.is_new()) return;

        frappe.call({
            method: "sc_custom.api.batch_storage.get_batch_qty_by_storage",
            args: {
                batch_no: frm.doc.name,
                item_code: frm.doc.item,
            },
            callback: function (r) {
                if (!r.message || !r.message.length) {
                    return;
                }

                const section = frm.dashboard.add_section("", __("Stock Levels"));

                // Sort by warehouse, then storage
                r.message.sort(function (a, b) {
                    if (a.warehouse === b.warehouse) {
                        return (a.storage || "").localeCompare(b.storage || "");
                    }
                    return a.warehouse.localeCompare(b.warehouse);
                });

                const rows = $("<div></div>").appendTo(section);

                // Header row
                $(`<div class='row' style='margin-bottom: 5px; font-weight: bold;'>
                    <div class='col-sm-3 small'>${__("Warehouse")}</div>
                    <div class='col-sm-2 small'>${__("Storage")}</div>
                    <div class='col-sm-2 small text-right'>${__("Qty")}</div>
                    <div class='col-sm-5'></div>
                </div>`).appendTo(rows);

                (r.message || []).forEach(function (d) {
                    $(`<div class='row' style='margin-bottom: 10px;'>
                        <div class='col-sm-3 small' style='padding-top: 3px;'>${d.warehouse}</div>
                        <div class='col-sm-2 small' style='padding-top: 3px;'>${d.storage || "-"}</div>
                        <div class='col-sm-2 small text-right' style='padding-top: 3px;'>${d.qty}</div>
                        <div class='col-sm-5'>
                            <button class='btn btn-default btn-xs btn-move' style='margin-right: 7px;'
                                data-qty="${d.qty}"
                                data-warehouse="${d.warehouse}">
                                ${__("Move")}</button>
                            <button class='btn btn-default btn-xs btn-split'
                                data-qty="${d.qty}"
                                data-warehouse="${d.warehouse}">
                                ${__("Split")}</button>
                        </div>
                    </div>`).appendTo(rows);
                });

                // Move button handler
                rows.find(".btn-move").on("click", function () {
                    const $btn = $(this);
                    frappe.prompt(
                        [
                            {
                                fieldname: "to_warehouse",
                                label: __("To Warehouse"),
                                fieldtype: "Link",
                                options: "Warehouse",
                            },
                        ],
                        (data) => {
                            frappe.call({
                                method: "erpnext.stock.doctype.stock_entry.stock_entry_utils.make_stock_entry",
                                args: {
                                    item_code: frm.doc.item,
                                    batch_no: frm.doc.name,
                                    qty: $btn.attr("data-qty"),
                                    from_warehouse: $btn.attr("data-warehouse"),
                                    to_warehouse: data.to_warehouse,
                                    source_document: frm.doc.reference_name,
                                    reference_doctype: frm.doc.reference_doctype,
                                },
                                callback: (r) => {
                                    frappe.show_alert(
                                        __("Stock Entry {0} created", [
                                            '<a href="/app/stock-entry/' +
                                                r.message.name +
                                                '">' +
                                                r.message.name +
                                                "</a>",
                                        ])
                                    );
                                    frm.refresh();
                                },
                            });
                        },
                        __("Select Target Warehouse"),
                        __("Move")
                    );
                });

                // Split button handler
                rows.find(".btn-split").on("click", function () {
                    const $btn = $(this);
                    frappe.prompt(
                        [
                            {
                                fieldname: "qty",
                                label: __("New Batch Qty"),
                                fieldtype: "Float",
                                default: $btn.attr("data-qty"),
                            },
                            {
                                fieldname: "new_batch_id",
                                label: __("New Batch ID (Optional)"),
                                fieldtype: "Data",
                            },
                        ],
                        (data) => {
                            frappe
                                .xcall("erpnext.stock.doctype.batch.batch.split_batch", {
                                    item_code: frm.doc.item,
                                    batch_no: frm.doc.name,
                                    qty: data.qty,
                                    warehouse: $btn.attr("data-warehouse"),
                                    new_batch_id: data.new_batch_id,
                                })
                                .then(() => frm.reload_doc());
                        },
                        __("Split Batch"),
                        __("Split")
                    );
                });

                frm.dashboard.show();
            },
        });
    },
});
