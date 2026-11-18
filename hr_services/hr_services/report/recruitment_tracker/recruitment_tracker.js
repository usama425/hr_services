// Copyright (c) 2026, Elite Resources and contributors
// For license information, please see license.txt

frappe.query_reports["Recruitment Tracker"] = {
	filters: [
		{ fieldname: "from_date", label: __("Offer Date From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Offer Date To"), fieldtype: "Date" },
		{ fieldname: "category", label: __("Candidate Category"), fieldtype: "Select", options: "\nOverseas\nLocal\nSaudi" },
		{ fieldname: "recruiter", label: __("Recruiter"), fieldtype: "Data" },
		{ fieldname: "stage", label: __("Stage"), fieldtype: "Link", options: "Workflow State" },
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },
		{ fieldname: "include_legacy", label: __("Include old offers (Legacy)"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && typeof data[column.fieldname] === "string" && data[column.fieldname].startsWith("No need with")) {
			return `<span class="text-muted">${value}</span>`;
		}
		return value;
	},
};
