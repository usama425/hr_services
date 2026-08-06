# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from hr_services.permissions.notification_access import throw_unless_administrator


class ERCTaskSettings(Document):
	def onload(self):
		"""Show the built-in defaults in the form when a template was never filled in."""
		from hr_services.cron_auto_email.task_notifications import apply_template_defaults

		apply_template_defaults(self)

	def validate(self):
		throw_unless_administrator()


def get_allowed_doctypes():
	"""Document types a task may be linked to, from Settings."""
	rows = frappe.get_all(
		"ERC Task Allowed Doctype",
		filters={"parent": "ERC Task Settings", "parenttype": "ERC Task Settings"},
		pluck="document_type",
	)

	return [dt for dt in rows if dt]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def allowed_doctype_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for ERC Task.reference_doctype - only the configured types."""
	allowed = get_allowed_doctypes()

	if not allowed:
		return []

	txt = (txt or "").lower()
	matches = [[dt] for dt in allowed if txt in dt.lower()]

	return matches[int(start) : int(start) + int(page_len)]
