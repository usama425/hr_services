// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.ui.form.on('PO Expiry Notification Settings', {
	refresh: function (frm) {
		frm.add_custom_button(__('Preview Recipients List'), function () {
			frappe.call({
				method: 'hr_services.cron_auto_email.po_expiry_notification.preview_po_expiry_notifications',
				args: { ignore_window: 1 },
				freeze: true,
				freeze_message: __('Checking employees...'),
				callback: function (r) {
					show_preview(r.message || {});
				}
			});
		});

		frm.add_custom_button(__('Send Now'), function () {
			const d = new frappe.ui.Dialog({
				title: __('Send PO Expiry Notifications'),
				fields: [
					{
						fieldtype: 'HTML',
						options: `<p>${__('This sends real emails to the configured recipients.')}</p>`
					},
					{
						fieldname: 'ignore_window',
						fieldtype: 'Check',
						label: __('Ignore the notice window (send even if the PO expiry is further away than the notice days)'),
						default: 0
					}
				],
				primary_action_label: __('Send'),
				primary_action: function (values) {
					d.hide();
					frappe.call({
						method: 'hr_services.cron_auto_email.po_expiry_notification.send_po_expiry_notifications_now',
						args: { ignore_window: values.ignore_window ? 1 : 0 },
						freeze: true,
						freeze_message: __('Sending notifications...'),
						callback: function (r) {
							const res = r.message || {};
							frappe.msgprint({
								title: __('Done'),
								indicator: 'green',
								message: __('Sent: {0} &nbsp; Skipped: {1} &nbsp; Failed: {2}',
									[res.sent || 0, res.skipped || 0, res.failed || 0])
							});
							frm.reload_doc();
						}
					});
				}
			});
			d.show();
		});
	}
});

function show_preview(data) {
	if (!data.enabled) {
		frappe.msgprint({
			title: __('Notifications Disabled'),
			indicator: 'orange',
			message: __('Reason: {0}', [frappe.utils.escape_html(data.reason || '')])
		});
		return;
	}

	const rows = data.employees || [];
	const header = `<p>${__('PO expiry date')}: <b>${frappe.utils.escape_html(data.po_expiry_date || '')}</b>
		&nbsp;(${data.days_remaining} ${__('days remaining')})<br>
		${__('To')}: <b>${frappe.utils.escape_html((data.recipients || []).join(', '))}</b><br>
		${__('CC')}: <b>${frappe.utils.escape_html((data.cc || []).join(', '))}</b></p>`;

	if (!rows.length) {
		frappe.msgprint({
			title: __('Nothing Due'),
			indicator: 'blue',
			message: header + `<p>${frappe.utils.escape_html(data.reason || __('No employee is due right now.'))}</p>`
		});
		return;
	}

	let html = header + `<table class="table table-bordered" style="font-size:12px;">
		<thead><tr>
			<th>${__('Employee')}</th><th>${__('Name')}</th>
			<th>${__('Type')}</th><th>${__('Reason')}</th>
		</tr></thead><tbody>`;

	rows.forEach(function (row) {
		html += `<tr>
			<td>${frappe.utils.escape_html(row.employee)}</td>
			<td>${frappe.utils.escape_html(row.employee_name || '')}</td>
			<td>${frappe.utils.escape_html(row.employment_type || '')}</td>
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
