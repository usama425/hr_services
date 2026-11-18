# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""One row per recruitment stage: how many new-flow Job Offers are there, by candidate
category, how long they have been in it, and how long finished offers took."""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today

STAGES = [
	"New Request",
	"Pending Candidate Signature",
	"Offer Signed",
	"Pending with Agency",
	"Visa Issued",
	"Transfer in Process",
	"Transfer Completed",
	"Contract Sent",
	"Employee Created",
	"Rejected by Candidate",
	"Withdrawn",
]
DONE = ("Employee Created", "Rejected by Candidate", "Withdrawn")
CATEGORIES = ("Overseas", "Local", "Saudi")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {"workflow_state": ["in", STAGES]}
	if filters.from_date and filters.to_date:
		conditions["creation"] = ["between", [filters.from_date, filters.to_date]]
	if filters.project:
		conditions["custom_project"] = filters.project
	if filters.category:
		conditions["custom_candidate_category"] = filters.category

	offers = frappe.get_all(
		"Job Offer",
		filters=conditions,
		fields=["workflow_state", "custom_candidate_category", "custom_stage_since", "modified", "custom_days_to_complete"],
		limit_page_length=0,
	)

	now = getdate(today())
	rows = []
	for stage in STAGES:
		in_stage = [o for o in offers if o.workflow_state == stage]
		row = {"stage": stage, "total": len(in_stage)}
		for c in CATEGORIES:
			row[c.lower()] = sum(1 for o in in_stage if o.custom_candidate_category == c)
		if stage in DONE:
			took = [o.custom_days_to_complete for o in in_stage if o.custom_days_to_complete is not None]
			row["avg_days_to_complete"] = flt(sum(took) / len(took), 1) if took and stage == "Employee Created" else None
		else:
			waits = [date_diff(now, getdate(o.custom_stage_since or o.modified)) for o in in_stage]
			row["avg_days_in_stage"] = flt(sum(waits) / len(waits), 1) if waits else None
			row["longest_wait"] = max(waits) if waits else None
		rows.append(row)

	return columns(), rows, None, chart(rows)


def columns():
	return [
		{"label": _("Stage"), "fieldname": "stage", "fieldtype": "Data", "width": 230},
		{"label": _("Overseas"), "fieldname": "overseas", "fieldtype": "Int", "width": 95},
		{"label": _("Local"), "fieldname": "local", "fieldtype": "Int", "width": 85},
		{"label": _("Saudi"), "fieldname": "saudi", "fieldtype": "Int", "width": 85},
		{"label": _("Total"), "fieldname": "total", "fieldtype": "Int", "width": 85},
		{"label": _("Avg Days in Stage"), "fieldname": "avg_days_in_stage", "fieldtype": "Float", "precision": 1, "width": 140},
		{"label": _("Longest Wait (Days)"), "fieldname": "longest_wait", "fieldtype": "Int", "width": 145},
		{"label": _("Avg Days to Complete"), "fieldname": "avg_days_to_complete", "fieldtype": "Float", "precision": 1, "width": 160},
	]


def chart(rows):
	return {
		"data": {
			"labels": [r["stage"] for r in rows],
			"datasets": [{"name": _(c), "values": [r[c.lower()] for r in rows]} for c in CATEGORIES],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"colors": ["#2490ef", "#29cd42", "#ffa00a"],
	}
