# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ContractExpiryNotificationLog(Document):
	"""Append-only record of every contract expiry email attempt.

	Rows are written by the scheduled job with ignore_permissions=True. They are
	also the state that drives the "first alert, then repeat every N days" logic,
	so nothing here should ever be edited or deleted by hand.
	"""

	pass
