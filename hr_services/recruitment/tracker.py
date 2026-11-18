# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""The recruitment tracker row: one per Job Offer, with the same columns (and
headers) as HR's recruitment Excel sheet. Used by the Recruitment Tracker report
and by the Microsoft 365 Excel sync, so both always show the same data."""

import frappe
from frappe.utils import formatdate, getdate, strip_html

NO_NEED = {"Overseas": "No need with Overseas", "Local": "No need with Local", "Saudi": "No need with Saudi"}

# (Excel header, report fieldname, report fieldtype, width, applies-to categories or None for all)
COLUMNS = [
	# HR's sheet keeps the Job Offer number in its "Job request" column; rows are matched on it.
	("Job request", "job_offer", "Link/Job Offer", 160, None),
	("Recruiter", "recruiter", "Data", 120, None),
	("Recrutment Typy", "category", "Data", 100, None),
	("Request DT", "request_date", "Date", 100, None),
	("Employee Name", "employee_name", "Data", 200, None),
	("Employee Email", "employee_email", "Data", 180, None),
	("Employee Number", "employee_number", "Data", 120, None),
	("Approved Salary", "approved_salary", "Currency", 110, None),
	("Position", "position", "Data", 160, None),
	("Nationality", "nationality", "Data", 100, None),
	("Location", "location", "Data", 100, None),
	("Name of the requester", "requester", "Data", 140, None),
	("Project Name", "project_name", "Data", 160, None),
	("ID Type", "id_type", "Data", 90, None),
	("ID Number", "id_number", "Data", 120, None),
	("Status", "status", "Data", 170, None),
	("VISA/Transfe", "visa_transfer", "Data", 100, None),
	("Last update (Date)", "last_update", "Date", 110, None),
	("Remark", "remark", "Data", 300, None),
	("Offer Sign DT", "offer_sign_date", "Date", 100, None),
	("Joining Date", "joining_date", "Date", 100, None),
	("iqama states", "iqama_status", "Data", 100, ("Local",)),
	("little NOL", "nol", "Data", 80, ("Local",)),
	("Iqama Profession", "iqama_profession", "Data", 160, ("Local",)),
	("Iqama Profession( Arabic)", "iqama_profession_ar", "Data", 160, ("Local",)),
	("Visa Name", "visa_name", "Data", 160, ("Overseas",)),
	("Visa Name (Arabic)", "visa_name_ar", "Data", 160, ("Overseas",)),
	("Visa Number", "visa_number", "Data", 110, ("Overseas",)),
	("Delegation (E- Wakala)", "e_wakala", "Data", 110, ("Overseas",)),
	("Contract tayp", "contract_type", "Data", 140, None),
	("Agency Name", "agency_name", "Data", 160, ("Overseas",)),
	("Agency Number", "agency_number", "Data", 120, ("Overseas",)),
	("Agency Email", "agency_email", "Data", 160, ("Overseas",)),
	("Country", "agency_country", "Data", 100, ("Overseas",)),
	# Stage dates (in the report; the Excel sheet only gets them if it has these headers)
	("Sent to Candidate On", "sent_to_candidate_on", "Date", 120, None),
	("Visa Issued / Transfer Completed On", "visa_transfer_done_on", "Date", 150, None),
	("Contract Sent On", "contract_sent_on", "Date", 120, None),
	("Completed On", "completed_on", "Date", 110, None),
	("Days to Complete", "days_to_complete", "Int", 110, None),
	("In Stage Since", "stage_since", "Date", 110, None),
]

KEY_HEADER = "Job request"

OFFER_FIELDS = [
	"name", "job_applicant", "applicant_name", "applicant_email", "designation", "status", "modified",
	"workflow_state", "custom_recruiter", "owner", "custom_candidate_category", "custom_job_requisition",
	"custom_total_salary", "custom_country", "custom_location", "custom_requester", "custom_project_name",
	"custom_id_type", "custom_id_number", "custom_visa_transfer_status", "custom_offer_signed_on",
	"custom_expected_joining_date", "custom_iqama_status", "custom_nol", "custom_iqama_profession",
	"custom_iqama_profession_ar", "custom_job_title_on_visa", "custom_job_title_on_visa_ar",
	"custom_visa_number", "custom_e_wakala", "custom_hiring_route", "custom_recruitment_agency",
	"custom_agency_contact_no", "custom_agency_email", "custom_agency_country",
	"custom_sent_to_candidate_on", "custom_visa_transfer_done_on", "custom_contract_sent_on",
	"custom_completed_on", "custom_days_to_complete", "custom_stage_since",
]


def report_columns():
	return [
		{"label": header, "fieldname": fieldname, "fieldtype": ftype.split("/")[0],
		 "options": ftype.split("/")[1] if "/" in ftype else None, "width": width}
		for header, fieldname, ftype, width, _applies in COLUMNS
	]


def get_offers(filters=None, names=None):
	conditions = {}
	if names:
		conditions["name"] = ["in", names]
	return frappe.get_all("Job Offer", filters=conditions or filters or {}, fields=OFFER_FIELDS,
						  order_by="creation desc", limit_page_length=0)


def build_rows(offers):
	"""Report rows (dicts keyed by report fieldname) for the given Job Offer records."""
	if not offers:
		return []

	applicants = {a.name: a for a in frappe.get_all(
		"Job Applicant", filters={"name": ["in", {o.job_applicant for o in offers if o.job_applicant}]},
		fields=["name", "phone_number"], limit_page_length=0)}
	requisitions = {r.name: r for r in frappe.get_all(
		"Job Requisition", filters={"name": ["in", {o.custom_job_requisition for o in offers if o.custom_job_requisition}]},
		fields=["name", "posting_date"], limit_page_length=0)}
	recruiters = {u.name: u.full_name for u in frappe.get_all(
		"User", filters={"name": ["in", {(o.custom_recruiter or o.owner) for o in offers}]},
		fields=["name", "full_name"], limit_page_length=0)}
	logs = _update_logs([o.name for o in offers])

	rows = []
	for o in offers:
		app = applicants.get(o.job_applicant) or frappe._dict()
		req = requisitions.get(o.custom_job_requisition) or frappe._dict()
		row = {
			"job_offer": o.name,
			"recruiter": recruiters.get(o.custom_recruiter or o.owner) or (o.custom_recruiter or o.owner),
			"category": o.custom_candidate_category,
			"request_date": req.posting_date,
			"employee_name": o.applicant_name,
			"employee_email": o.applicant_email,
			"employee_number": app.phone_number,
			"approved_salary": o.custom_total_salary,
			"position": o.designation,
			"nationality": o.custom_country,
			"location": o.custom_location,
			"requester": o.custom_requester,
			"project_name": o.custom_project_name,
			"id_type": o.custom_id_type,
			"id_number": o.custom_id_number,
			"status": o.workflow_state if o.workflow_state and not o.workflow_state.startswith("Legacy") else o.status,
			"visa_transfer": o.custom_visa_transfer_status,
			"last_update": getdate(o.modified),
			"remark": logs.get(o.name, ""),
			"offer_sign_date": o.custom_offer_signed_on,
			"joining_date": o.custom_expected_joining_date,
			"iqama_status": o.custom_iqama_status,
			"nol": o.custom_nol,
			"iqama_profession": o.custom_iqama_profession,
			"iqama_profession_ar": o.custom_iqama_profession_ar,
			"visa_name": o.custom_job_title_on_visa,
			"visa_name_ar": o.custom_job_title_on_visa_ar,
			"visa_number": o.custom_visa_number,
			"e_wakala": o.custom_e_wakala,
			"contract_type": o.custom_hiring_route,
			"agency_name": o.custom_recruitment_agency,
			"agency_number": o.custom_agency_contact_no,
			"agency_email": o.custom_agency_email,
			"agency_country": o.custom_agency_country,
			"sent_to_candidate_on": o.custom_sent_to_candidate_on,
			"visa_transfer_done_on": o.custom_visa_transfer_done_on,
			"contract_sent_on": o.custom_contract_sent_on,
			"completed_on": o.custom_completed_on,
			"days_to_complete": o.custom_days_to_complete,
			"stage_since": o.custom_stage_since,
		}
		# Columns that don't apply to this candidate show "No need with <category>", as in the sheet.
		no_need = NO_NEED.get(o.custom_candidate_category)
		if no_need:
			for _h, fieldname, _t, _w, applies in COLUMNS:
				if applies and o.custom_candidate_category not in applies:
					row[fieldname] = no_need
		rows.append(row)
	return rows


def _update_logs(offer_names):
	"""Remark column: the update log as dated lines, oldest first."""
	out = {}
	for r in frappe.get_all(
		"Job Offer Update",
		filters={"parent": ["in", offer_names], "parenttype": "Job Offer"},
		fields=["parent", "updated_on", "details"],
		order_by="updated_on asc",
		limit_page_length=0,
	):
		line = f"{formatdate(r.updated_on, 'dd-MMM')}: {strip_html(r.details or '').strip()}"
		out[r.parent] = f"{out[r.parent]}\n{line}" if r.parent in out else line
	return out


EXCEL_EPOCH = getdate("1899-12-30")


def excel_value(value):
	"""Cell value for Microsoft Graph. Dates go as Excel serial numbers, like the
	sheet's own dates, so the column's date format still applies."""
	if value is None:
		return ""
	if hasattr(value, "isoformat"):
		return (getdate(value) - EXCEL_EPOCH).days
	return value
