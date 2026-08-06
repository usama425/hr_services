// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

const INTERNAL_USER_FILTERS = {
	enabled: 1,
	user_type: 'System User',
	name: ['like', '%@eliteresources.co']
};

frappe.ui.form.on('ERC Task', {
	setup: function (frm) {
		frm.set_query('assigned_to', function () {
			return { filters: INTERNAL_USER_FILTERS };
		});

		frm.set_query('user', 'cc_users', function () {
			return { filters: INTERNAL_USER_FILTERS };
		});

		frm.set_query('reference_doctype', function () {
			return {
				query: 'hr_services.hr_services.doctype.erc_task_settings.erc_task_settings.allowed_doctype_query'
			};
		});
	},

	refresh: function (frm) {
		if (frm.is_new()) return;

		if (frm.doc.status !== 'Completed' && frm.doc.status !== 'Cancelled') {
			frm.add_custom_button(__('Mark Complete'), function () {
				frm.set_value('status', 'Completed').then(() => frm.save());
			});

			frm.add_custom_button(__('Reassign'), function () {
				reassign_dialog(frm);
			});
		}

		if (frm.doc.reference_doctype && frm.doc.reference_name) {
			frm.add_custom_button(__('Open Related Record'), function () {
				frappe.set_route('Form', frm.doc.reference_doctype, frm.doc.reference_name);
			});
		}

		show_progress(frm);
	},

	checklist_on_form_rendered: function (frm) {
		show_progress(frm);
	}
});

frappe.ui.form.on('ERC Task Checklist Item', {
	completed: function (frm) {
		// Recompute locally so the bar moves before the save round-trip.
		const rows = frm.doc.checklist || [];
		const done = rows.filter((r) => r.completed).length;
		frm.doc.progress = rows.length ? (done * 100) / rows.length : 0;
		show_progress(frm);
	}
});

function show_progress(frm) {
	frm.dashboard.clear_headline();

	const rows = frm.doc.checklist || [];
	if (!rows.length) return;

	const done = rows.filter((r) => r.completed).length;
	const pct = Math.round((done * 100) / rows.length);

	frm.dashboard.add_progress(__('Checklist'), [
		{
			title: __('{0} of {1} done', [done, rows.length]),
			width: pct + '%',
			progress_class: pct === 100 ? 'progress-bar-success' : 'progress-bar-info'
		}
	]);
}

function reassign_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __('Reassign Task'),
		fields: [
			{
				fieldname: 'assigned_to',
				fieldtype: 'Link',
				label: __('Reassign To'),
				options: 'User',
				reqd: 1,
				get_query: () => ({ filters: INTERNAL_USER_FILTERS })
			},
			{
				fieldname: 'reason',
				fieldtype: 'Data',
				label: __('Reason'),
				reqd: 1
			}
		],
		primary_action_label: __('Reassign'),
		primary_action: function (values) {
			if (values.assigned_to === frm.doc.assigned_to) {
				frappe.msgprint(__('That is already the assignee.'));
				return;
			}

			d.hide();
			frm.set_value('assigned_to', values.assigned_to);
			frappe.call({
				method: 'hr_services.hr_services.doctype.erc_task.erc_task.set_reassign_reason',
				args: { task: frm.doc.name, reason: values.reason },
				freeze: true,
				callback: () => frm.reload_doc()
			});
		}
	});
	d.show();
}
