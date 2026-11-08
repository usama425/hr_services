# Copyright (c) 2026, ERC IT and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TeamsExcelWatchState(Document):
	"""Starting point and pending changes for one watched file (managed by the app)."""
