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
		generate_invoices,
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

	# Scope to THIS Payroll Entry's own submitted, un-invoiced slips (exactly the
	# employees in the generated sheet) — NOT get_employees(project), which would
	# also bill other sub-schools/work-locations sharing the same project.
	slips = frappe.get_all(
		"Salary Slip",
		filters={"payroll_entry": doc.name, "docstatus": 1, "invoice_created": 0},
		fields=["name", "employee", "employee_name"])
	if not slips:
		return
	# generate_invoices expects the child-row shape: 'employee' (not 'name').
	payload = [{
		"employee": s["employee"],
		"employee_name": s.get("employee_name"),
		"salary_slip": s["name"],
	} for s in slips]

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

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_payroll_entries_for_jv(doctype, txt, searchfield, start, page_len, filters):
	"""Payroll Entries that can still be linked on a Journal Entry row.

	HRMS v16 hides every Payroll Entry already referenced by any Journal Entry, which
	includes the accrual JV that HRMS itself creates, so almost nothing can be picked
	for the salary payment JV. Instead, list a submitted Payroll Entry while the
	salaries accrued on its payroll payable account are not fully paid, and every
	submitted Payroll Entry that has no Journal Entry yet.
	"""
	if searchfield not in ("name", "posting_date", "company", "payroll_payable_account"):
		searchfield = "name"
	return frappe.db.sql(
		f"""
		select pe.name
		from `tabPayroll Entry` pe
		left join (
			select jea.reference_name, jea.account,
				sum(jea.credit_in_account_currency - jea.debit_in_account_currency) as balance
			from `tabJournal Entry Account` jea
			join `tabJournal Entry` je on je.name = jea.parent and je.docstatus = 1
			where jea.reference_type = 'Payroll Entry'
			group by jea.reference_name, jea.account
		) linked on linked.reference_name = pe.name and linked.account = pe.payroll_payable_account
		where pe.docstatus = 1
			and pe.`{searchfield}` like %(txt)s
			and (
				linked.balance > 0.01
				or not exists (
					select 1 from `tabJournal Entry Account` x
					join `tabJournal Entry` xj on xj.name = x.parent and xj.docstatus = 1
					where x.reference_type = 'Payroll Entry' and x.reference_name = pe.name
				)
			)
		order by pe.name desc
		limit %(page_len)s offset %(start)s
		""",
		{"txt": f"%{txt}%", "page_len": page_len, "start": start},
	)
