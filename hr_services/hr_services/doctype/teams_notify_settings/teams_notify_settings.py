# Copyright (c) 2026, ERC IT and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


class TeamsNotifySettings(Document):
	def validate(self):
		self.tenant_id = (self.tenant_id or "").strip()
		self.client_id = (self.client_id or "").strip()
		if self.client_id and not GUID.match(self.client_id):
			frappe.throw(_("The Application (client) ID is a GUID such as 1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d. Copy it from the app's Overview page."))
		if self.tenant_id and not (GUID.match(self.tenant_id) or "." in self.tenant_id):
			frappe.throw(_("The Directory (tenant) ID is a GUID. Copy it from the app's Overview page."))
		self.quiet_minutes = max(1, cint(self.quiet_minutes) or 5)
		self.max_wait_minutes = max(self.quiet_minutes, cint(self.max_wait_minutes) or 30)
		self.max_changes_per_card = min(60, max(1, cint(self.max_changes_per_card) or 25))
		self.max_cards_per_update = min(10, max(1, cint(self.max_cards_per_update) or 3))
		self.max_file_mb = min(200, max(1, cint(self.max_file_mb) or 25))
		self.max_cells = max(1000, cint(self.max_cells) or 500000)

	def is_configured(self) -> bool:
		return bool(self.tenant_id and self.client_id and self.get_password("client_secret", raise_exception=False))
