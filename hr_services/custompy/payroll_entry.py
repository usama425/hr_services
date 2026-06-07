# Copyright (c) 2023, Elite Resources and contributors
# For license information, please see license.txt

import frappe
import json
from hrms.payroll.doctype.payroll_entry.payroll_entry import PayrollEntry,get_existing_salary_slips


ARABIC_MONTHS = {
	1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
	7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}


def auto_generate_invoice_on_approval(doc, method=None):
	"""When a Payroll Entry reaches 'Approved by FM', auto-create the client
	Sales Invoice(s) by reusing the existing Payroll Invoices Generator logic
	(routed by the project's invoice_type). Never blocks the approval.

	PO-management projects (project.custom_allow_po_management) are skipped —
	they need per-employee working days entered manually."""
	if doc.get("workflow_state") != "Approved by FM":
		return
	if not doc.get("projects"):
		return
	try:
		_auto_generate_invoice(doc)
	except Exception:
		frappe.log_error(
			title=f"Auto Sales Invoice failed: {doc.name}",
			message=frappe.get_traceback(),
		)


def _auto_generate_invoice(doc):
	from frappe.utils import getdate, flt
	from hr_services.hr_services.doctype.payroll_invoices_generator.payroll_invoices_generator import (
		get_employees, generate_invoices,
	)

	project = doc.projects
	p = frappe.db.get_value(
		"Project", project,
		["customer", "invoice_type", "custom_allow_po_management"],
		as_dict=True) or {}
	invoice_type, customer = p.get("invoice_type"), p.get("customer")
	if not invoice_type or not customer:
		frappe.log_error(
			title=f"Auto Sales Invoice skipped: {doc.name}",
			message=f"Project {project} has no invoice_type/customer set.")
		return

	# Already billed for this Payroll Entry? Don't double-invoice.
	if frappe.db.exists("Sales Invoice",
			{"custom_payroll_entry_link": doc.name, "docstatus": ["!=", 2]}):
		return

	# PO-management projects need manual working days per employee.
	if p.get("custom_allow_po_management"):
		frappe.log_error(
			title=f"Auto Sales Invoice skipped (PO project): {doc.name}",
			message=(f"Project {project} uses PO Management — create the client "
					 f"invoice manually (per-employee working days required)."))
		return

	start, end = getdate(doc.start_date), getdate(doc.end_date)
	month_name = end.strftime("%B")
	year = str(end.year)
	due_date = end
	my_in_arabic = f"{ARABIC_MONTHS.get(end.month, '')} {year}".strip()

	employees = get_employees(project, start, end, month_name) or []
	if not employees:
		return
	# generate_invoices expects the child-row shape: 'employee' (not 'name').
	payload = [{
		"employee": e["name"],
		"employee_name": e.get("employee_name"),
		"salary_slip": e.get("salary_slip"),
	} for e in employees]

	generate_invoices(project, due_date, customer, invoice_type,
					   json.dumps(payload), month_name, my_in_arabic, year)

	# Check & balance: surface the created invoice total for verification
	# against the generated Elite sheet's billing total.
	sis = frappe.get_all(
		"Sales Invoice",
		filters={"custom_payroll_entry_link": doc.name, "docstatus": ["!=", 2]},
		fields=["name", "grand_total"])
	total = sum(flt(s.grand_total) for s in sis)
	doc.add_comment("Comment", (
		f"Auto Sales Invoice: {len(sis)} invoice(s) for {len(payload)} employee(s), "
		f"grand total {total:,.2f} (incl. VAT). Verify against the Elite sheet "
		f"billing total."))


def create_slips_on_submit(doc, method=None):
	"""Build Salary Slips when a Payroll Entry reaches the 'Slips Created'
	workflow state.

	The "Create Salary Slips" workflow action only submits the PE (a Frappe
	workflow transition changes state/docstatus, it does NOT call HRMS's
	create_salary_slips). Slip creation used to rely on a manual HRMS core
	patch that Frappe Cloud wipes on every rebuild — so we trigger it here,
	from the app, where it survives deploys.

	Guarded so it never double-creates: skips if slips were already created or
	any non-cancelled slip already exists for this PE.
	"""
	if doc.get("workflow_state") != "Slips Created":
		return
	if not doc.get("employees"):
		return
	# Already-submitted slips for this PE? Leave them alone (don't recreate).
	if frappe.db.exists("Salary Slip", {"payroll_entry": doc.name, "docstatus": 1}):
		return
	# Remove any leftover DRAFT slips (e.g. from a failed/retried attempt) so we
	# never end up with duplicate slips for the same employee + period.
	for name in frappe.get_all("Salary Slip",
			filters={"payroll_entry": doc.name, "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Salary Slip", name, force=True, ignore_permissions=True)
	# HRMS method: creates draft Salary Slips for doc.employees (enqueues a
	# background job when there are many employees) and sets salary_slips_created.
	doc.create_salary_slips()

@frappe.whitelist()
def get_totals(self):
	#loading the frm data
	self = json.loads(self)
	#adding the employee id into emps list
	# emps = []
	# for emp in self["employees"]:
	# 	emps.append(emp["employee"])
	#getting the sum of gross pay, sum of total deductions, sum of net pay of all salary slips
	totals = frappe.db.sql("""
							SELECT 
								payroll_entry, 
								SUM(gross_pay) as total_gross,
						 		SUM(total_deduction) as total_deduction,
								SUM(net_pay) as total_net_pay,
								SUM(total_loan_repayment) as total_loan_repayment
							FROM
								`tabSalary Slip`
							WHERE 
					 			docstatus != 2
					 			AND payroll_entry = %s
							""", (self["name"]),
							as_dict=1)
	return totals

@frappe.whitelist()
def create_delete_salary_slip(payroll_name):
	payroll_doc = frappe.get_doc("Payroll Entry", payroll_name)

	employee_count = frappe.db.sql("""
		SELECT COUNT(*)
		FROM `tabPayroll Employee Detail`
		WHERE parent = %s
	""", payroll_name)[0][0]

	frappe.db.set_value("Payroll Entry", payroll_name, {"number_of_employees": employee_count}, update_modified=False)
	frappe.db.commit()

	employees = [emp.employee for emp in payroll_doc.employees]
	if employees:
		args = frappe._dict(
			{
				"salary_slip_based_on_timesheet": payroll_doc.salary_slip_based_on_timesheet,
				"payroll_frequency": payroll_doc.payroll_frequency,
				"start_date": payroll_doc.start_date,
				"end_date": payroll_doc.end_date,
				"company": payroll_doc.company,
				"posting_date": payroll_doc.posting_date,
				"deduct_tax_for_unclaimed_employee_benefits": payroll_doc.deduct_tax_for_unclaimed_employee_benefits,
				"deduct_tax_for_unsubmitted_tax_exemption_proof": payroll_doc.deduct_tax_for_unsubmitted_tax_exemption_proof,
				"payroll_entry": payroll_doc.name,
				"exchange_rate": payroll_doc.exchange_rate,
				"currency": payroll_doc.currency,
			}
		)

	salary_slips_exist_for = get_existing_salary_slips(employees, args)

	for emp in employees:
		if emp not in salary_slips_exist_for:
			args.update({"doctype": "Salary Slip", "employee": emp})
			frappe.get_doc(args).insert()

	salary_slips_not_in = get_existing_salary_slips_not_in(employees,args)

	for ss in salary_slips_not_in:
		frappe.delete_doc("Salary Slip",ss)

	frappe.msgprint("Updated successfully.")	


def get_existing_salary_slips_not_in(employees, args):
	return frappe.db.sql_list(
		"""
		select distinct name from `tabSalary Slip`
		where docstatus!= 2 and company = %s and payroll_entry = %s
			and start_date >= %s and end_date <= %s
			and employee not in (%s)
	"""
		% ("%s", "%s", "%s", "%s", ", ".join(["%s"] * len(employees))),
		[args.company, args.payroll_entry, args.start_date, args.end_date] + employees,
	)	