# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Employees with status "Under Recruitment" are still being hired and must never
be paid. They are left out of every payroll employee picker, and any Payroll Entry
or Salary Slip that still contains one fails to save, whichever path created it
(Get Employees, ERC Posting Run, Update Salary Slips, or by hand)."""

import frappe
from frappe import _

UNDER_RECRUITMENT = "Under Recruitment"


def get_under_recruitment(employees):
	"""Return {employee: employee_name} for the given IDs whose status is Under Recruitment."""
	employees = [e for e in set(employees) if e]
	if not employees:
		return {}

	return dict(
		frappe.get_all(
			"Employee",
			filters={"name": ["in", employees], "status": UNDER_RECRUITMENT},
			fields=["name", "employee_name"],
			as_list=True,
		)
	)


def exclude_under_recruitment(emp_list):
	"""Drop Under Recruitment employees from a payroll employee list (rows with an `employee` key)."""
	if not emp_list:
		return emp_list

	blocked = get_under_recruitment([d.employee for d in emp_list])
	return [d for d in emp_list if d.employee not in blocked]


def throw_if_under_recruitment(employees, doctype):
	blocked = get_under_recruitment(employees)
	if not blocked:
		return

	listed = ", ".join(f"{emp} ({name})" for emp, name in sorted(blocked.items()))
	frappe.throw(
		_(
			"Payroll cannot be processed for employees with status {0}: {1}. "
			"Remove them from this {2} or change their status first."
		).format(UNDER_RECRUITMENT, listed, _(doctype)),
		title=_("Employee Under Recruitment"),
	)


def validate_payroll_entry(doc, method=None):
	throw_if_under_recruitment([d.employee for d in doc.employees], doc.doctype)


def validate_salary_slip(doc, method=None):
	throw_if_under_recruitment([doc.employee], doc.doctype)
