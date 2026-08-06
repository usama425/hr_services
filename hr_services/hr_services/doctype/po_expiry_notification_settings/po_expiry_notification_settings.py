# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from hr_services.permissions.notification_access import throw_unless_administrator


class POExpiryNotificationSettings(Document):
	def onload(self):
		"""Show the built-in defaults in the form when a template was never filled in."""
		from hr_services.cron_auto_email.po_expiry_notification import apply_template_defaults

		apply_template_defaults(self)

	def validate(self):
		# The desk form is already limited to Administrator by the role permission and the
		# has_permission hook; this also covers direct API writes.
		throw_unless_administrator()

		if flt(self.remaining_units_threshold) <= 0:
			frappe.throw(_("Remaining Units Threshold must be greater than zero."))

		from hr_services.cron_auto_email.po_expiry_notification import parse_recipients

		if not parse_recipients(self.recipients):
			frappe.throw(_("At least one recipient email address is required."))
