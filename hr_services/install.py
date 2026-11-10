# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

import json
import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def before_install():
	# custom/job_requisition.json adds a Document Link that points at
	# Job Applicant.custom_job_requisition, which is defined in custom/job_applicant.json.
	# Frappe syncs the custom/*.json files in directory order, so on a fresh install
	# the link can be validated before the field exists. Create the field first.
	path = frappe.get_app_path("hr_services", "hr_services", "custom", "job_applicant.json")
	with open(path) as f:
		data = json.load(f)

	for field in data["custom_fields"]:
		if field["fieldname"] == "custom_job_requisition":
			df = {k: v for k, v in field.items() if k not in ("name", "dt", "creation", "modified", "owner", "modified_by", "docstatus")}
			create_custom_field("Job Applicant", df, ignore_validate=True, is_system_generated=False)
