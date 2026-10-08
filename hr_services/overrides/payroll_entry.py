from hrms.payroll.doctype.payroll_entry.payroll_entry import PayrollEntry

from hr_services.custompy.under_recruitment import exclude_under_recruitment


class CustomPayrollEntry(PayrollEntry):
	def get_emp_list(self):
		# Core only skips status 'Inactive', so Get Employees would otherwise
		# pull Under Recruitment employees into payroll.
		return exclude_under_recruitment(super().get_emp_list())
