// Copyright (c) 2026, ERC IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Teams Notify Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test connection"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first."));
				return;
			}
			frappe
				.call({
					method: "hr_services.teams_excel_notify.api.test_connection",
					freeze: true,
					freeze_message: __("Asking Microsoft for a token…"),
				})
				.then((r) => {
					if (!r.message) return;
					frappe.msgprint({
						title: r.message.ok ? __("Connected") : __("One more step"),
						indicator: r.message.ok ? "green" : "orange",
						message: r.message.message,
					});
				});
		});
		frm.add_custom_button(__("Watched files"), () => frappe.set_route("List", "Teams Excel Watch"));
		frm.add_custom_button(__("Change log"), () => frappe.set_route("List", "Teams Excel Change Log"));
		render_overview(frm);
	},
});

function render_overview(frm) {
	const field = frm.get_field("overview_html");
	if (!field) return;
	frappe.call("hr_services.teams_excel_notify.api.get_overview").then((r) => {
		const o = r.message || {};
		let checks;
		if (o.scheduler_disabled) {
			checks = `<span class="indicator-pill red">${__("The scheduler is disabled on this site")}</span>`;
		} else if (o.scheduler_ok) {
			checks = `<span class="indicator-pill green">${__("Running")}</span> &nbsp;${__("last run")} ${frappe.datetime.comment_when(o.last_poll)}`;
		} else {
			checks = `<span class="indicator-pill orange">${__("No run in the last 10 minutes")}</span>`;
		}
		const rows = [
			[__("Background checks (every minute)"), checks],
			[__("Watched files"), o.watches || 0],
			[__("Changes waiting to be sent"), o.pending || 0],
			[__("Files with an error"), o.errors || 0],
			[__("Messages that failed to post"), o.failed_logs || 0],
		];
		field.$wrapper.html(
			`<table class="table table-bordered" style="max-width: 680px">${rows
				.map(([k, v]) => `<tr><td style="width: 50%">${k}</td><td>${v}</td></tr>`)
				.join("")}</table>`
		);
	});
}
