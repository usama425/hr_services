// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.query_reports["Recruitment Status Summary"] = {
	filters: [
		{ fieldname: "from_date", label: __("Created From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Created To"), fieldtype: "Date" },
		{ fieldname: "category", label: __("Candidate Category"), fieldtype: "Select", options: "\nOverseas\nLocal\nSaudi" },
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },
	],
	onload(report) {
		report.page.add_inner_button(__("Open Job Offers"), () => frappe.set_route("List", "Job Offer"));
	},
};
