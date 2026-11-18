# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Job Offer recruitment flow.

Stages are driven by the "Job Offer Recruitment" workflow. This module:
- checks the fields each stage needs before an offer can enter it,
- keeps the old Applicant Type / Nationality fields in sync with Candidate Category,
- stamps the offer sign date and logs every "Latest Update" with user and date,
- creates the Employee (status Under Recruitment) when HR clicks "Create Employee"
  at Visa Issued / Transfer Completed.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, today

from hr_services.custompy.under_recruitment import UNDER_RECRUITMENT

WORKFLOW_FIELD = "workflow_state"
LEGACY = "Legacy"  # old offers: Legacy Draft / Legacy / Legacy Cancelled


def is_legacy(state):
	return (state or "").startswith(LEGACY)
EMPLOYEE_CREATED = "Employee Created"
OFFER_SIGNED = "Offer Signed"

# HR clicks "Create Employee" at these stages.
READY_FOR_EMPLOYEE = ("Visa Issued", "Transfer Completed")

OVERSEAS, LOCAL, SAUDI = "Overseas", "Local", "Saudi"
QIWA_ACCEPTED = ("Accepted by Candidate", "Accepted by His/Her Sponsor")

# Order of the forward path; a stage needs everything the stages before it need.
STAGE_RANK = {
	"New Request": 0,
	"Pending Candidate Signature": 1,
	OFFER_SIGNED: 2,
	"Pending with Agency": 3,
	"Transfer in Process": 3,
	"Visa Issued": 4,
	"Transfer Completed": 4,
	EMPLOYEE_CREATED: 5,
}


def _required_fields(doc):
	"""(rank, fieldname, label, check) for this offer's category. check(value) -> ok."""
	category = doc.get("custom_candidate_category")
	filled = bool
	rules = [
		(1, "custom_candidate_category", _("Candidate Category"), filled),
		(1, "designation", _("Designation"), filled),
		(1, "custom_project", _("Project"), filled),
		(1, "offer_date", _("Offer Date"), filled),
		(1, "custom_total_salary", _("Total Salary"), lambda v: flt(v) > 0),
		(1, "custom_recruiter", _("Recruiter"), filled),
		(1, "custom_id_type", _("ID Type"), filled),
		(1, "custom_id_number", _("ID Number"), filled),
		(2, "custom_signed_job_offer", _("Signed Job Offer"), filled),
		# These sit in the Qiwa section, which shows once the offer is signed (Accepted).
		(3, "custom_country", _("Country (Nationality)"), filled),
		(3, "custom_current_city", _("Current City"), filled),
	]
	if category == OVERSEAS:
		rules += [
			(3, "custom_recruitment_agency", _("Agency Name"), filled),
			(3, "custom_agency_country", _("Agency Country"), filled),
			(4, "custom_job_title_on_visa", _("Visa Name"), filled),
			(4, "custom_job_title_on_visa_ar", _("Visa Name (Arabic)"), filled),
			(4, "custom_visa_number", _("Visa Number"), filled),
			(4, "custom_visa_authorization", _("Visa Authorization"), filled),
		]
	elif category in (LOCAL, SAUDI):
		if category == LOCAL:
			rules += [
				(3, "custom_iqama_status", _("Iqama Status"), filled),
				(3, "custom_nol", _("NOL"), filled),
				(4, "custom_iqama_profession", _("Iqama Profession"), filled),
				(4, "custom_iqama_profession_ar", _("Iqama Profession (Arabic)"), filled),
			]
		rules.append((4, "custom_qiwa_status", _("Qiwa Status (Accepted)"), lambda v: v in QIWA_ACCEPTED))
		if category == SAUDI:
			rules.append((4, "custom_gosi_status", _("Gosi Status (Accepted)"), lambda v: v == "Accepted"))
	return rules


# --------------------------------------------------------------------------- Job Offer hooks


def validate(doc, method=None):
	"""Runs on draft saves (validate) and on submitted saves (before_update_after_submit)."""
	if is_legacy(doc.get(WORKFLOW_FIELD)):
		check_new_request(doc)
		if cint(doc.get(NEW_REQUEST_FIELD)):
			sync_legacy_fields(doc)
			set_defaults(doc)
		log_latest_update(doc)
		return

	# The form enforces this too, but its rule is JavaScript and the server can't evaluate it.
	if not doc.get("custom_candidate_category"):
		frappe.throw(_("Candidate Category is required."), frappe.MandatoryError, title=_("Missing Fields"))
	sync_legacy_fields(doc)
	set_defaults(doc)
	check_stage_requirements(doc)
	stamp_dates(doc)
	create_employee_at_stage(doc)
	log_latest_update(doc)


def sync_legacy_fields(doc):
	"""Old code, prints and the Create Contract mapping still read these two fields."""
	category = doc.get("custom_candidate_category")
	if not category:
		return
	doc.custom_applicant_type = "Overseas" if category == OVERSEAS else "Local"
	doc.custom_nationality = "Saudi" if category == SAUDI else "Non Saudi"

	if category == SAUDI:
		doc.custom_country = "Saudi Arabia"
	elif not doc.custom_country or doc.custom_country == "Saudi Arabia":
		# Country used to default to Saudi Arabia for everyone; take the applicant's.
		country = doc.job_applicant and frappe.db.get_value("Job Applicant", doc.job_applicant, "custom_nationality")
		doc.custom_country = country if country and country != "Saudi Arabia" else None


def set_defaults(doc):
	if not doc.get("custom_recruiter"):
		# Free text; start with the creator's name, HR can type any name.
		doc.custom_recruiter = frappe.utils.get_fullname(doc.owner or frappe.session.user)
	if not doc.get("custom_hiring_route"):
		doc.custom_hiring_route = {OVERSEAS: "MOFA", LOCAL: "Sponsorship Transfer", SAUDI: "Saudi Hire"}.get(
			doc.get("custom_candidate_category")
		)
	if doc.get("custom_recruitment_agency") and not (doc.get("custom_agency_email") or doc.get("custom_agency_contact_no")):
		email, phone = frappe.db.get_value("Supplier", doc.custom_recruitment_agency, ["email_id", "mobile_no"]) or (None, None)
		doc.custom_agency_email = doc.custom_agency_email or email
		doc.custom_agency_contact_no = doc.custom_agency_contact_no or phone


def check_stage_requirements(doc):
	rank = STAGE_RANK.get(doc.get(WORKFLOW_FIELD))
	if not rank:
		return
	missing = [
		label
		for need_rank, fieldname, label, ok in _required_fields(doc)
		if need_rank <= rank and not ok(doc.get(fieldname))
	]
	if missing:
		frappe.throw(
			_("Before moving this offer to <b>{0}</b>, fill in:<br>{1}").format(
				_(doc.get(WORKFLOW_FIELD)), "<br>".join(f"• {m}" for m in missing)
			),
			title=_("Missing information"),
		)


def create_employee_at_stage(doc):
	"""HR clicked "Create Employee": make the Under Recruitment employee from this offer.
	Runs after the stage checks, so the offer has everything the employee needs."""
	if doc.get(WORKFLOW_FIELD) != EMPLOYEE_CREATED or doc.get("custom_employee"):
		return
	employee = create_employee_from_offer(doc)
	doc.custom_employee = employee  # the save that follows must keep the link
	frappe.msgprint(
		_("Employee {0} created with status {1}.").format(
			frappe.utils.get_link_to_form("Employee", employee), UNDER_RECRUITMENT
		),
		alert=True,
		indicator="green",
	)


def stamp_dates(doc):
	if STAGE_RANK.get(doc.get(WORKFLOW_FIELD), 0) >= STAGE_RANK[OFFER_SIGNED] and not doc.get("custom_offer_signed_on"):
		doc.custom_offer_signed_on = today()


def log_latest_update(doc):
	text = (doc.get("custom_latest_update") or "").strip()
	if not text:
		return
	before = doc.get_doc_before_save()
	if before and (before.get("custom_latest_update") or "").strip() == text:
		return
	doc.append(
		"custom_update_log",
		{
			"updated_on": now_datetime(),
			"updated_by": frappe.session.user,
			"stage": doc.get(WORKFLOW_FIELD),
			"details": text,
		},
	)


def on_change(doc, method=None):
	if doc.docstatus < 2 and is_legacy(doc.get(WORKFLOW_FIELD)) and cint(doc.get(NEW_REQUEST_FIELD)):
		# HR ticked "New Request" on an old offer: it joins the new flow at the stage it has reached.
		_set_offer_state(doc.name, _entry_state(doc), _("Moved into the new recruitment flow"))
		return

	from hr_services.recruitment.excel_sync import enqueue_offer_sync

	enqueue_offer_sync(doc.name)


NEW_REQUEST_FIELD = "custom_new_request"


def on_cancel(doc, method=None):
	"""Frappe's own Cancel doesn't move the workflow stage; keep it in step."""
	state = doc.get(WORKFLOW_FIELD)
	if state in ("Legacy Cancelled", "Withdrawn"):
		return
	if is_legacy(state):
		doc.db_set(WORKFLOW_FIELD, "Legacy Cancelled", update_modified=False)
	else:
		doc.set(WORKFLOW_FIELD, "Withdrawn")
		_set_offer_state(doc.name, "Withdrawn", _("Job Offer cancelled"))


def check_new_request(doc):
	"""Old offer with "New Request" ticked: make sure it can join the new flow."""
	if not cint(doc.get(NEW_REQUEST_FIELD)):
		return
	if doc.get(WORKFLOW_FIELD) == "Legacy Cancelled":
		frappe.throw(_("A cancelled offer can't be moved into the new flow."), title=_("New Request"))
	if not doc.get("custom_candidate_category"):
		frappe.throw(
			_("Select the Candidate Category before ticking New Request."), frappe.MandatoryError, title=_("New Request")
		)


def _entry_state(doc):
	"""Where an old offer starts in the new flow."""
	if doc.docstatus == 0:
		return "New Request"
	if doc.get("custom_employee") or frappe.db.exists("Employee", {"job_offer": doc.name}):
		return EMPLOYEE_CREATED
	if doc.status == "Rejected":
		return "Rejected by Candidate"
	if doc.status == "Accepted" or doc.get("custom_signed_job_offer"):
		return OFFER_SIGNED
	return "Pending Candidate Signature"


# --------------------------------------------------------------------------- Stage changes by the system


def _set_offer_state(offer_name, state, note):
	"""System-driven stage change (an old offer joining the flow, a cancel): the state is
	written directly instead of going through workflow validation."""
	frappe.db.set_value("Job Offer", offer_name, WORKFLOW_FIELD, state)
	idx = (frappe.db.count("Job Offer Update", {"parent": offer_name, "parenttype": "Job Offer"}) or 0) + 1
	frappe.get_doc(
		{
			"doctype": "Job Offer Update",
			"parent": offer_name,
			"parenttype": "Job Offer",
			"parentfield": "custom_update_log",
			"idx": idx,
			"updated_on": now_datetime(),
			"updated_by": frappe.session.user,
			"stage": state,
			"details": note,
		}
	).db_insert()
	frappe.get_doc("Job Offer", offer_name).add_comment("Workflow", _(state))

	from hr_services.recruitment.excel_sync import enqueue_offer_sync

	enqueue_offer_sync(offer_name)


# --------------------------------------------------------------------------- Employee creation


SALARY_MAP = {
	"Basic Salary": "basic_salary",
	"Housing Allowance": "housing_allowance",
	"Transportations Allowance": "transport_allowance",
}


def create_employee_from_offer(offer):
	"""Create (once) the Under Recruitment employee for this offer (a Job Offer doc or name).
	Returns the Employee name."""
	if isinstance(offer, str):
		offer = frappe.get_doc("Job Offer", offer)

	existing = offer.get("custom_employee") or frappe.db.get_value(
		"Employee", {"job_offer": offer.name}, "name"
	) or (offer.job_applicant and frappe.db.get_value("Employee", {"job_applicant": offer.job_applicant}, "name"))
	if existing:
		if offer.get("custom_employee") != existing:
			frappe.db.set_value("Job Offer", offer.name, "custom_employee", existing, update_modified=False)
		return existing

	applicant = frappe.db.get_value(
		"Job Applicant",
		offer.job_applicant,
		["applicant_name", "email_id", "phone_number", "resume_attachment"],
		as_dict=True,
	) or frappe._dict()

	emp = frappe.new_doc("Employee")
	emp.update(
		{
			"first_name": offer.applicant_name or applicant.applicant_name,
			"name_in_arabic": offer.get("custom_applicant_name_in_arabic"),
			"salutation": offer.get("custom_salutation"),
			"personal_email": offer.applicant_email or applicant.email_id,
			"cell_number": applicant.phone_number,
			"company": offer.company,
			"designation": offer.designation,
			"project": offer.get("custom_project"),
			"nationality": offer.get("custom_country"),
			"date_of_joining": offer.get("custom_expected_joining_date"),
			"scheduled_confirmation_date": offer.get("custom_expected_joining_date"),
			"job_applicant": offer.job_applicant,
			"job_offer": offer.name,
			"status": UNDER_RECRUITMENT,
			"custom_jo": offer.get("custom_signed_job_offer"),
			"custom_cv": applicant.resume_attachment,
		}
	)

	id_type, id_number = offer.get("custom_id_type"), offer.get("custom_id_number")
	if id_type == "Passport":
		emp.passport_number = id_number
	elif id_type in ("Iqama", "National ID"):
		emp.iqama_national_id = id_number
	rows = offer.get("custom_salary_details") or []
	other = 0
	for row in rows:
		field = SALARY_MAP.get(row.salary_components)
		if field:
			emp.set(field, flt(row.amount))
		else:
			other += flt(row.amount)
	emp.food_allowance = other
	emp.ctc = flt(offer.get("custom_total_salary"))

	# Personal details (date of birth, gender, ...) are collected later; they become
	# required when HR changes the status from Under Recruitment to Active.
	emp.flags.ignore_mandatory = True
	emp.flags.ignore_permissions = True
	emp.insert()

	frappe.db.set_value("Job Offer", offer.name, "custom_employee", emp.name, update_modified=False)
	return emp.name


def relax_mandatory_while_under_recruitment(doc, method=None):
	"""Employee validate: personal details are optional until the employee is activated."""
	if doc.status == UNDER_RECRUITMENT:
		doc.flags.ignore_mandatory = True
