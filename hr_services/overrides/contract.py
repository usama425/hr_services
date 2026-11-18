# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

import frappe
from erpnext.crm.doctype.contract.contract import Contract

# Party types whose name field isn't "<party type in lower case>_name".
PARTY_NAME_FIELD = {"Job Applicant": "applicant_name"}


class CustomContract(Contract):
	def set_missing_values(self):
		"""ERPNext reads "<party type>_name" from the party, which for the "Job Applicant"
		party type hr_services adds is "job applicant_name" and fails the save
		("Invalid field format for SELECT")."""
		field = PARTY_NAME_FIELD.get(self.party_type)
		if not field:
			return super().set_missing_values()
		if (
			not self.party_full_name
			or self.has_value_changed("party_name")
			or self.has_value_changed("party_type")
		):
			if res := frappe.db.get_value(self.party_type, self.party_name, field):
				self.party_full_name = res
