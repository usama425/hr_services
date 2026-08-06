// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.listview_settings['ERC Task'] = {
	add_fields: ['status', 'due_date', 'assigned_to', 'assigned_by', 'priority', 'progress'],

	get_indicator: function (doc) {
		const today = frappe.datetime.get_today();

		if (doc.status === 'Completed') {
			return [__('Completed'), 'green', 'status,=,Completed'];
		}
		if (doc.status === 'Cancelled') {
			return [__('Cancelled'), 'gray', 'status,=,Cancelled'];
		}
		if (doc.due_date && doc.due_date < today) {
			return [__('Overdue'), 'red', 'status,not in,Completed,Cancelled'];
		}
		if (doc.status === 'In Progress') {
			return [__('In Progress'), 'blue', 'status,=,In Progress'];
		}
		return [__('Open'), 'orange', 'status,=,Open'];
	},

	onload: function (listview) {
		const me = frappe.session.user;
		const today = frappe.datetime.get_today();

		const apply = function (filters) {
			listview.filter_area.clear().then(function () {
				filters.forEach(function (f) {
					listview.filter_area.add(f[0], f[1], f[2], f[3]);
				});
				listview.refresh();
			});
		};

		const views = [
			{
				label: __('My Tasks'),
				filters: [['ERC Task', 'assigned_to', '=', me]]
			},
			{
				label: __('My Assigned Tasks'),
				filters: [['ERC Task', 'assigned_by', '=', me]]
			},
			{
				label: __('Not Done'),
				filters: [['ERC Task', 'status', 'not in', ['Completed', 'Cancelled']]]
			},
			{
				label: __('Completed'),
				filters: [['ERC Task', 'status', '=', 'Completed']]
			},
			{
				label: __('Overdue'),
				filters: [
					['ERC Task', 'due_date', '<', today],
					['ERC Task', 'status', 'not in', ['Completed', 'Cancelled']]
				]
			}
		];

		views.forEach(function (view) {
			listview.page.add_inner_button(view.label, function () {
				apply(view.filters);
			});
		});

		listview.page.add_inner_button(__('Clear Filters'), function () {
			listview.filter_area.clear().then(() => listview.refresh());
		});
	}
};
