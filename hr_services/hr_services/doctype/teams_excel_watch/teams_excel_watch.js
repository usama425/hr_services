// Copyright (c) 2026, ERC IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Teams Excel Watch", {
	refresh(frm) {
		const webhook = frm.get_field("webhook_url");
		if (webhook && webhook.disable_password_checks) webhook.disable_password_checks();
		if (frm.is_new()) return;

		const run = (method, message) =>
			frappe
				.call({
					method: `hr_services.teams_excel_notify.api.${method}`,
					args: { watch: frm.doc.name },
					freeze: true,
					freeze_message: message,
				})
				.then((r) => {
					show_result(r.message);
					frm.reload_doc();
				});

		const actions = __("Actions");
		frm.add_custom_button(__("Check now"), () => run("check_now", __("Checking the file…")), actions);
		frm.add_custom_button(
			__("Send pending changes now"),
			() => run("send_pending_now", __("Comparing and posting to Teams…")),
			actions
		);
		frm.add_custom_button(
			__("Send test message"),
			() =>
				frappe
					.call({
						method: "hr_services.teams_excel_notify.api.send_test_message",
						args: { watch: frm.doc.name },
						freeze: true,
						freeze_message: __("Posting to Teams…"),
					})
					.then((r) => r.message && frappe.show_alert({ message: r.message, indicator: "green" }, 8)),
			actions
		);
		frm.add_custom_button(__("Reconnect file from link"), () => run("resolve_file", __("Finding the file…")), actions);
		frm.add_custom_button(
			__("Start over from current file"),
			() =>
				frappe.confirm(
					__(
						"Use the file as it is now as the new starting point? Changes made since the last message will not be sent."
					),
					() => run("reset_baseline", __("Reading the file…"))
				),
			actions
		);
		frm.add_custom_button(__("Change log"), () =>
			frappe.set_route("List", "Teams Excel Change Log", { watch: frm.doc.name })
		);
		if (frm.doc.file_web_url) {
			frm.add_custom_button(__("Open file"), () => window.open(frm.doc.file_web_url, "_blank"));
		}
		show_status(frm);
	},
});

function show_result(result) {
	if (!result) return;
	if (result.file_name) {
		frappe.show_alert({ message: __("Connected to {0}", [result.file_name]), indicator: "green" }, 8);
		return;
	}
	const colors = { error: "red", send_failed: "red", busy: "orange", pending: "orange" };
	const indicator = colors[result.outcome] || "green";
	if (indicator === "red") {
		frappe.msgprint({ title: __("Check failed"), indicator, message: frappe.utils.escape_html(result.message) });
	} else {
		frappe.show_alert({ message: frappe.utils.escape_html(result.message), indicator }, 10);
	}
}

function show_status(frm) {
	frappe.call({ method: "hr_services.teams_excel_notify.api.get_status", args: { watch: frm.doc.name } }).then((r) => {
		const s = r.message || {};
		if (frm.doc.status === "Error" && frm.doc.last_error) {
			frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.last_error), "red");
		} else if (s.session_editors && s.send_after) {
			frm.dashboard.set_headline_alert(
				__("{0} edited the file. Changes will be posted around {1} if nobody keeps editing.", [
					frappe.utils.escape_html(s.session_editors),
					frappe.datetime.str_to_user(s.send_after),
				]),
				"orange"
			);
		} else if (s.baseline_taken_at) {
			frm.dashboard.set_headline_alert(
				__("Watching. Last checked {0}.", [frappe.datetime.comment_when(s.last_checked_at || s.baseline_taken_at)]),
				"green"
			);
		} else if (frm.doc.enabled) {
			frm.dashboard.set_headline_alert(
				__("Waiting for the first check (runs every minute). Use Actions → Check now to run it now."),
				"blue"
			);
		}
	});
}
