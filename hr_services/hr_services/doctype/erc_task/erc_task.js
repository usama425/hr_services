// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

// Read on the User doctype is locked down on this site, so a plain filtered link
// search returns only the caller. Both pickers go through a server query that
// reads the internal directory directly.
const INTERNAL_USER_QUERY =
	'hr_services.hr_services.doctype.erc_task.erc_task.internal_user_query';

frappe.ui.form.on('ERC Task', {
	setup: function (frm) {
		// Both are Table MultiSelect fields. Their control extends ControlLink and has
		// no `.grid`, so the three-argument child-table form of set_query throws and
		// takes the whole form render down with it - the query goes on the parent
		// fieldname instead.
		frm.set_query('assignees', function () {
			return { query: INTERNAL_USER_QUERY };
		});

		frm.set_query('cc_users', function () {
			return { query: INTERNAL_USER_QUERY };
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
	const current = (frm.doc.assignees || []).map((r) => r.user);

	const d = new frappe.ui.Dialog({
		title: __('Reassign Task'),
		fields: [
			{
				fieldname: 'assignees',
				fieldtype: 'MultiSelectPills',
				label: __('Assign To'),
				reqd: 1,
				default: current,
				get_data: function (txt) {
					return frappe.call({
						method: 'hr_services.hr_services.doctype.erc_task.erc_task.internal_user_options',
						args: { txt: txt }
					}).then(function (r) {
						return r.message || [];
					});
				}
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
			const chosen = values.assignees || [];

			if (!chosen.length) {
				frappe.msgprint(__('Pick at least one person.'));
				return;
			}

			d.hide();
			frappe.call({
				method: 'hr_services.hr_services.doctype.erc_task.erc_task.reassign',
				args: { task: frm.doc.name, assignees: chosen, reason: values.reason },
				freeze: true,
				freeze_message: __('Reassigning...'),
				callback: () => frm.reload_doc()
			});
		}
	});
	d.show();
}
