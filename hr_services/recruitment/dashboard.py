# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Data for the Recruitment Status Board on the Recruitment Dashboard."""

import frappe
from frappe import _

IN_PROGRESS = [
	"New Request",
	"Pending Candidate Signature",
	"Offer Signed",
	"Pending with Agency",
	"Visa Issued",
	"Transfer in Process",
	"Transfer Completed",
	"Contract Sent",
]
FINISHED = ["Employee Created", "Rejected by Candidate", "Withdrawn"]
OLD = ["Legacy Draft", "Legacy", "Legacy Cancelled"]
CATEGORIES = ("Overseas", "Local", "Saudi")


def offer_filters(filters):
	"""Job Offer list filters for the board's controls (also used by the page for its links)."""
	f = []
	if filters.get("category"):
		f.append(["custom_candidate_category", "=", filters["category"]])
	if filters.get("project"):
		f.append(["custom_project", "=", filters["project"]])
	if filters.get("recruiter"):
		f.append(["custom_recruiter", "=", filters["recruiter"]])
	if filters.get("from_date"):
		f.append(["creation", ">=", filters["from_date"]])
	if filters.get("to_date"):
		f.append(["creation", "<=", filters["to_date"] + " 23:59:59"])
	return f


@frappe.whitelist()
def status_board(filters=None):
	filters = frappe.parse_json(filters or {}) or {}
	states = IN_PROGRESS + FINISHED + (OLD if filters.get("include_old") else [])
	rows = frappe.get_list(
		"Job Offer",
		filters=[["workflow_state", "in", states], *offer_filters(filters)],
		fields=["workflow_state", "custom_candidate_category", "status", {"COUNT": "*", "as": "n"}],
		group_by="workflow_state, custom_candidate_category, status",
		limit_page_length=0,
	)

	def stage(name, group):
		mine = [r for r in rows if r.workflow_state == name]
		entry = {"stage": name, "group": group, "count": sum(r.n for r in mine)}
		for c in CATEGORIES:
			entry[c.lower()] = sum(r.n for r in mine if r.custom_candidate_category == c)
		if group == "old":
			entry["by_status"] = {}
			for r in mine:
				entry["by_status"][r.status or _("Not Set")] = entry["by_status"].get(r.status or _("Not Set"), 0) + r.n
		return entry

	board = [stage(s, "in_progress") for s in IN_PROGRESS] + [stage(s, "finished") for s in FINISHED]
	if filters.get("include_old"):
		board += [stage(s, "old") for s in OLD]
	return {"stages": board, "options": _options()}


def _options():
	"""Choices for the board's dropdowns: what new-flow offers actually use."""
	new_flow = [["workflow_state", "not like", "Legacy%"]]
	recruiters = frappe.get_list("Job Offer", filters=new_flow, pluck="custom_recruiter", distinct=True, limit_page_length=0)
	projects = frappe.get_list(
		"Job Offer", filters=new_flow, fields=["custom_project", "custom_project_name"], distinct=True, limit_page_length=0
	)
	return {
		"recruiters": sorted({r for r in recruiters if r}),
		"projects": sorted(
			({"value": p.custom_project, "label": p.custom_project_name or p.custom_project} for p in projects if p.custom_project),
			key=lambda p: p["label"],
		),
	}


# --------------------------------------------------------------------------- the board itself

BOARD = "Recruitment Status Board"
BOARD_ROLES = ("HR User", "HR Manager", "HR REC User", "Elite HR User", "Elite REC User")


def sync_status_board():
	"""Create / update the "Recruitment Status Board" Custom HTML Block from the files
	in recruitment/status_board (after_migrate), so the app is its source."""
	path = frappe.get_app_path("hr_services", "recruitment", "status_board")
	read = lambda name: open(f"{path}/{name}", encoding="utf-8").read()
	block = frappe.get_doc("Custom HTML Block", BOARD) if frappe.db.exists("Custom HTML Block", BOARD) else frappe.new_doc("Custom HTML Block")
	block.update({"html": read("board.html"), "style": read("board.css"), "script": read("board.js"), "private": 0})
	if block.is_new():
		block.name = BOARD
		block.flags.name_set = True
		for role in BOARD_ROLES:
			block.append("roles", {"role": role})
		block.insert(ignore_permissions=True, set_name=BOARD)
	else:
		block.save(ignore_permissions=True)
	return block.name
