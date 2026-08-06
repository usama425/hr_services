// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.ui.form.on('Contract Expiry Notification Settings', {
	refresh: function (frm) {
		frm.add_custom_button(__('Preview Recipients List'), function () {
			frappe.call({
				method: 'hr_services.cron_auto_email.contract_expiry_notification.preview_contract_expiry_notifications',
				freeze: true,
				freeze_message: __('Checking employees...'),
				callback: function (r) {
					show_preview(r.message || {});
				}
			});
		});

		frm.add_custom_button(__('Send Now'), function () {
			frappe.confirm(
				__('This sends real emails to the configured recipients. Continue?'),
				function () {
					frappe.call({
						method: 'hr_services.cron_auto_email.contract_expiry_notification.send_contract_expiry_notifications_now',
						freeze: true,
						freeze_message: __('Sending notifications...'),
						callback: function (r) {
							const d = r.message || {};
							frappe.msgprint({
								title: __('Done'),
								indicator: 'green',
								message: __('Sent: {0} &nbsp; Skipped: {1} &nbsp; Failed: {2}',
									[d.sent || 0, d.skipped || 0, d.failed || 0])
							});
							frm.reload_doc();
						}
					});
				}
			);
		});
	}
});

function show_preview(data) {
	const rows = data.employees || [];

	if (!data.enabled) {
		frappe.msgprint({
			title: __('Notifications Disabled'),
			indicator: 'orange',
			message: __('Reason: {0}', [frappe.utils.escape_html(data.reason || '')])
		});
		return;
	}

	if (!rows.length) {
		frappe.msgprint({
			title: __('Nothing Due'),
			indicator: 'blue',
			message: __('No employee is due for a contract expiry notification right now.')
		});
		return;
	}

	let html = `<p>${__('Recipients')}: <b>${frappe.utils.escape_html((data.recipients || []).join(', '))}</b></p>
		<table class="table table-bordered" style="font-size:12px;">
		<thead><tr>
			<th>${__('Employee')}</th><th>${__('Name')}</th>
			<th>${__('Contract End')}</th><th>${__('Days')}</th><th>${__('Reason')}</th>
		</tr></thead><tbody>`;

	rows.forEach(function (row) {
		const indicator = row.is_overdue ? 'style="color:#c0392b;font-weight:bold;"' : '';
		html += `<tr>
			<td>${frappe.utils.escape_html(row.employee)}</td>
			<td>${frappe.utils.escape_html(row.employee_name || '')}</td>
			<td>${frappe.utils.escape_html(row.contract_end_date || '')}</td>
			<td ${indicator}>${row.days_remaining}</td>
			<td>${frappe.utils.escape_html(row.reason || '')}</td>
		</tr>`;
	});

	html += '</tbody></table>';

	frappe.msgprint({
		title: __('{0} employee(s) would be emailed', [rows.length]),
		indicator: 'blue',
		message: html,
		wide: true
	});
}
