from frappe import _

from hr_services.lending_compat import has_lending


def get_dashboard_for_employee(data):
	data["transactions"].extend(
		[
			{"label": _("Purchase Invoice"), "items": ["Purchase Invoice"]},
			{"label": _("Invoice to Client"), "items": ["Sales Invoice"]},
		]
	)
	data["non_standard_fieldnames"].update({"Purchase Invoice": "employee_no", "Sales Invoice": "employee_id"})

	if has_lending():
		data["transactions"].append({"label": _("Employee Advance"), "items": ["Loan"]})
		data["non_standard_fieldnames"]["Loan"] = "applicant"
	return data

def get_dashboard_for_payroll_entry(data):
	data["transactions"].extend(
		[
			{"items": ["Sales Invoice"]},
		]
	)

	data["non_standard_fieldnames"].update(
		{"Sales Invoice": "custom_payroll_entry_link"}
	)
	return data

def get_data_for_job_applicant(data):
	data["transactions"] = 	[	
			{"items": ["Employee"]},
			{"items": ["Job Offer"]},
			{"items": ["Contract"]},
		]
	return data
