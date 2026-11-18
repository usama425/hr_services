#Copyright (c) 2023, Elite Resources and contributors
#For license information, please see license.txt

import frappe
from frappe.model.mapper import get_mapped_doc
import json

@frappe.whitelist()
def make_employee(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.personal_email, target.first_name = frappe.db.get_value(
			"Job Applicant", source.job_applicant, ["email_id", "applicant_name"]
		)
		source_doc = frappe.get_doc("Job Offer",source_name)
		others = 0
		for sd in source_doc.custom_salary_details:
			if sd.salary_components == "Basic Salary":
				target.basic_salary = sd.amount
			elif sd.salary_components == "Housing Allowance":
				target.housing_allowance = sd.amount
			elif sd.salary_components == "Transportations Allowance":
				target.transport_allowance = sd.amount
			else:
				others += sd.amount
		target.food_allowance = others
		target.ctc = source_doc.custom_total_salary
	doc = get_mapped_doc(
		"Job Offer",
		source_name,
		{
			"Job Offer": {
				"doctype": "Employee",
				"field_map": {"applicant_name": "employee_name",
				  			"custom_applicant_name_in_arabic": "name_in_arabic",
							"offer_date": "scheduled_confirmation_date",
							"custom_project": "project",
							"custom_salutation": "salutation",
							"custom_country": "nationality"},
			}
		},
		target_doc,
		set_missing_values,
	)
	return doc


@frappe.whitelist()
def make_contract(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.party_type = "Job Applicant"
		if source.custom_contract_duration == "1 Year":
			target.custom_contract_duration = 1
		elif source.custom_contract_duration == "2 Year":
			target.custom_contract_duration = 2
		# The candidate's ID goes to the matching contract field.
		if source.get("custom_id_number"):
			if source.get("custom_id_type") == "Passport":
				target.custom_passport_no = target.custom_passport_no or source.custom_id_number
			else:
				target.custom_id_iqama_no = target.custom_id_iqama_no or source.custom_id_number
		if source.get("custom_project"):
			target.custom_customer = frappe.db.get_value("Project", source.custom_project, "customer")
		if source.get("job_applicant"):
			target.custom_mobile_no = target.custom_mobile_no or frappe.db.get_value(
				"Job Applicant", source.job_applicant, "phone_number"
			)

	doc = get_mapped_doc(
		"Job Offer",
		source_name,
		{
			"Job Offer": {
				"doctype": "Contract",
				"field_map": {"name": "custom_job_offer",
							"job_applicant": "party_name",
							"applicant_name": "custom_c_name",
							"custom_applicant_name_in_arabic": "custom_name_in_arabic",
							"custom_country": "custom_nationality",
							"designation": "custom_designation",
							"custom_contract_type": "custom_contract_type",
							"applicant_email": "custom_email",
							"custom_job_title_on_visa_ar": "custom_job_title_in_arabic",
				},
			},
			"Contract Details": {"doctype": "Contract Details"},
		},
		target_doc,
		set_missing_values,
	)
	return doc


# Contract fields filled from the Job Offer when HR picks the offer on a Contract.
CONTRACT_FIELDS_FROM_OFFER = (
	"party_type", "party_name", "custom_c_name", "custom_name_in_arabic", "custom_nationality",
	"custom_designation", "custom_contract_type", "custom_project", "custom_project_name", "custom_customer",
	"custom_job_title_on_visa", "custom_job_title_in_arabic", "custom_visa_number", "custom_total_salary",
	"custom_contract_duration", "custom_passport_no", "custom_id_iqama_no", "custom_email", "custom_mobile_no",
)


@frappe.whitelist()
def contract_values_from_offer(job_offer):
	"""What a Contract gets from its Job Offer, for the Contract form to fill in (all editable)."""
	frappe.has_permission("Job Offer", "read", job_offer, throw=True)
	doc = make_contract(job_offer)
	values = {f: doc.get(f) for f in CONTRACT_FIELDS_FROM_OFFER if doc.get(f) not in (None, "")}
	values["salary_detail"] = [
		{"salary_components": r.salary_components, "amount": r.amount, "description": r.description,
		 "custom_display_text": r.get("custom_display_text")} for r in doc.get("salary_detail") or []
	]
	return values


@frappe.whitelist()
def get_terms_and_conditions(template_name, doc):
	if isinstance(doc, str):
		doc = json.loads(doc)

	terms_and_conditions = frappe.get_doc("Terms and Conditions", template_name)

	if terms_and_conditions.custom_terms_and_conditions_in_arabic:
		return frappe.render_template(terms_and_conditions.custom_terms_and_conditions_in_arabic, doc)
