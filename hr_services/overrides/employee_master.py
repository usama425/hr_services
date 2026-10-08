from hrms.overrides.employee_master import EmployeeMaster

from hr_services.custompy.under_recruitment import UNDER_RECRUITMENT


class CustomEmployeeMaster(EmployeeMaster):
	def validate(self):
		# ERPNext's Employee.validate rejects any status outside a hard-coded list
		# (Active/Inactive/Suspended/Left). Run the core checks as Inactive, which
		# matches Under Recruitment (not on payroll, user need not be enabled).
		if self.status != UNDER_RECRUITMENT:
			return super().validate()

		self.status = "Inactive"
		try:
			super().validate()
		finally:
			self.status = UNDER_RECRUITMENT
