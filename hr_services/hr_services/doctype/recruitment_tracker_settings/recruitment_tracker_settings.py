# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class RecruitmentTrackerSettings(Document):
	def validate(self):
		if not self.workbook_link:
			self.drive_id = self.item_id = self.file_name = None
		elif self.has_value_changed("workbook_link") or not self.item_id:
			from hr_services.recruitment.excel_sync import connect_workbook

			connect_workbook(self)
		if self.enabled and not self.item_id:
			frappe.throw(frappe._("Add the Excel file link before enabling the sync."))
