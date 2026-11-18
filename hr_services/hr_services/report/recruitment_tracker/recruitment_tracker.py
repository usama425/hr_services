# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Recruitment Tracker: HR's recruitment Excel sheet, live from the system."""

from hr_services.recruitment.tracker import build_rows, get_offers, report_columns


def execute(filters=None):
	filters = filters or {}
	conditions = {"docstatus": ["<", 2]}
	from_date, to_date = filters.get("from_date"), filters.get("to_date")
	if from_date and to_date:
		conditions["offer_date"] = ["between", [from_date, to_date]]
	elif from_date:
		conditions["offer_date"] = [">=", from_date]
	elif to_date:
		conditions["offer_date"] = ["<=", to_date]
	for key, field in (
		("category", "custom_candidate_category"),
		("stage", "workflow_state"),
		("project", "custom_project"),
	):
		if filters.get(key):
			conditions[field] = filters[key]
	if filters.get("recruiter"):
		conditions["custom_recruiter"] = ["like", f"%{filters['recruiter']}%"]
	if not filters.get("include_legacy") and not filters.get("stage"):
		conditions["workflow_state"] = ["not like", "Legacy%"]

	return report_columns(), build_rows(get_offers(conditions))
