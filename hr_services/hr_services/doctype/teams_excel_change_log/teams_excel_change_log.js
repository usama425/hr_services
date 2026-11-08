// Copyright (c) 2026, ERC IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Teams Excel Change Log", {
	refresh(frm) {
		const label = frm.doc.status === "Sent" ? __("Post to Teams again") : __("Retry posting to Teams");
		frm.add_custom_button(label, () =>
			frappe
				.call({
					method: "hr_services.teams_excel_notify.api.resend_log",
					args: { log: frm.doc.name },
					freeze: true,
					freeze_message: __("Posting to Teams…"),
				})
				.then((r) => {
					if (r.message) frappe.show_alert({ message: r.message, indicator: "green" }, 6);
					frm.reload_doc();
				})
		);
	},
});
