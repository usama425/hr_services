// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.ui.form.on('PO Expiry Notification Settings', {
	refresh: function (frm) {
		frm.add_custom_button(__('Preview PO List'), function () {
			frappe.call({
				method: 'hr_services.cron_auto_email.po_expiry_notification.preview_po_expiry_notifications',
				freeze: true,
				freeze_message: __('Checking purchase orders...'),
				callback: function (r) {
					show_preview(r.message || {});
				}
			});
		});

		frm.add_custom_button(__('Send Now'), function () {
			frappe.confirm(
				__('This sends real emails for any PO at or below the threshold that has not been emailed yet. Continue?'),
				function () {
					frappe.call({
						method: 'hr_services.cron_auto_email.po_expiry_notification.send_po_expiry_notifications_now',
						freeze: true,
						freeze_message: __('Sending notifications...'),
						callback: function (r) {
							const res = r.message || {};
							frappe.msgprint({
								title: __('Done'),
								indicator: 'green',
								message: __('Sent: {0} &nbsp; Already notified: {1} &nbsp; Failed: {2}',
									[res.sent || 0, res.skipped || 0, res.failed || 0])
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
	if (!data.enabled) {
		frappe.msgprint({
			title: __('Notifications Disabled'),
			indicator: 'orange',
			message: __('Reason: {0}', [frappe.utils.escape_html(data.reason || '')])
		});
		return;
	}

	const rows = data.pos || [];
	const header = `<p>${__('Threshold')}: <b>${data.threshold} ${__('remaining units or fewer')}</b><br>
		${__('To')}: <b>${frappe.utils.escape_html((data.recipients || []).join(', '))}</b>
		${(data.cc || []).length ? `<br>${__('CC')}: <b>${frappe.utils.escape_html((data.cc || []).join(', '))}</b>` : ''}</p>`;

	if (!rows.length) {
		frappe.msgprint({
			title: __('Nothing Due'),
			indicator: 'blue',
			message: header + `<p>${frappe.utils.escape_html(data.reason || __('No PO is due right now.'))}</p>`
		});
		return;
	}

	let html = header + `<table class="table table-bordered" style="font-size:12px;">
		<thead><tr>
			<th>${__('PO')}</th><th>${__('PO No')}</th><th>${__('Employee')}</th>
			<th>${__('Units')}</th><th>${__('Used')}</th><th>${__('Remaining')}</th>
		</tr></thead><tbody>`;

	rows.forEach(function (row) {
		const style = row.is_exhausted ? 'style="color:#c0392b;font-weight:bold;"' : '';
		html += `<tr>
			<td>${frappe.utils.escape_html(row.po)}</td>
			<td>${frappe.utils.escape_html(row.po_no || '')}</td>
			<td>${frappe.utils.escape_html(row.employee_name || row.employee)}</td>
			<td>${row.po_units}</td>
			<td>${row.used_units}</td>
			<td ${style}>${row.remaining_units}</td>
		</tr>`;
	});

	html += '</tbody></table>';

	frappe.msgprint({
		title: __('{0} PO(s) would be emailed', [rows.length]),
		indicator: 'blue',
		message: html,
		wide: true
	});
}
