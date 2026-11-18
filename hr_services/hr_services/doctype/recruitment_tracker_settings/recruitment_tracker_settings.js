// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.ui.form.on("Recruitment Tracker Settings", {
	refresh(frm) {
		if (!frm.doc.enabled || !frm.doc.item_id) return;
		frm.add_custom_button(__("Test Connection"), () =>
			frappe.call({
				method: "hr_services.recruitment.excel_sync.test_connection",
				freeze: true,
				callback: (r) => r.message && frappe.msgprint(r.message),
			})
		);
		frm.add_custom_button(__("Sync All Offers Now"), () =>
			frappe.confirm(__("Write every offer's row to the Excel sheet now?"), () =>
				frappe.call({
					method: "hr_services.recruitment.excel_sync.sync_all",
					callback: () => frappe.show_alert({ message: __("Sync started in the background."), indicator: "green" }),
				})
			)
		);
	},
});
