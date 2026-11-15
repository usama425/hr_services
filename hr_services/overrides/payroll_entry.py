import frappe
from frappe import _
from hrms.payroll.doctype.payroll_entry.payroll_entry import PayrollEntry

from hr_services.custompy.under_recruitment import exclude_under_recruitment


class CustomPayrollEntry(PayrollEntry):
	@frappe.whitelist()
	def fill_employee_details(self):
		# HRMS v15+ fetches employees here (get_emp_list no longer exists). Core only
		# skips status 'Inactive' and knows nothing about our Payroll Entry `projects`
		# field, so narrow the core list instead of patching HRMS:
		#   - drop Under Recruitment employees (never paid)
		#   - keep only employees of the selected project, if one is set
		super().fill_employee_details()

		employees = exclude_under_recruitment(self.employees)
		if self.get("projects"):
			in_project = set(
				frappe.get_all(
					"Employee",
					filters={"name": ("in", [d.employee for d in employees]), "project": self.projects},
					pluck="name",
				)
			)
			employees = [d for d in employees if d.employee in in_project]

		if len(employees) != len(self.employees):
			if not employees:
				frappe.throw(
					_("No employees found for the mentioned criteria:<br>Company: {0}<br>Project: {1}").format(
						frappe.bold(self.company), frappe.bold(self.get("projects") or "-")
					),
					title=_("No employees found"),
				)

			self.set("employees", [])
			for d in employees:
				self.append("employees", d.as_dict(no_default_fields=True))
			self.number_of_employees = len(self.employees)

		return self.get_employees_with_unmarked_attendance()

	def validate_payroll_payable_account(self):
		# HRMS v16 insists the payroll payable account is of type "Payable". ERPNext then
		# demands Party Type/Party (Employee) on every JV row hitting that account, which
		# breaks our bank entries and manual accrual JVs unless employee-wise payroll
		# accounting is enabled. We keep 2230101 Accrued Expenses - Salaries with no account
		# type (as for all payrolls before the upgrade), so accept a blank type as well.
		account_type = frappe.db.get_value("Account", self.payroll_payable_account, "account_type")
		if account_type not in (None, "", "Payable"):
			frappe.throw(
				_("Payroll payable account {0} must have account type Payable or no account type").format(
					frappe.bold(self.payroll_payable_account)
				)
			)
