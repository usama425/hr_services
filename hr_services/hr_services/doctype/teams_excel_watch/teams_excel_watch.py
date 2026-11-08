# Copyright (c) 2026, ERC IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from hr_services.teams_excel_notify.differ import DiffConfig, column_letter
from hr_services.teams_excel_notify.graph import GraphError

# Written by the background check; a form save must not overwrite them.
STATUS_FIELDS = ("status", "last_checked_at", "last_notified_at", "last_error", "pending_editors")
# Changing any of these makes the next check take a fresh starting point.
RESET_FIELDS = (
	"enabled",
	"drive_id",
	"item_id",
	"header_row",
	"label_columns",
	"sheets",
	"ignore_columns",
	"ignore_formula_cells",
	"include_hidden_sheets",
)


class TeamsExcelWatch(Document):
	def validate(self):
		self.file_link = (self.file_link or "").strip()
		self.drive_id = (self.drive_id or "").strip()
		self.item_id = (self.item_id or "").strip()
		if cint(self.header_row) < 0:
			frappe.throw(_("Header row can't be negative."))
		try:
			cfg = DiffConfig.build(self.header_row, self.label_columns or "A", self.ignore_columns)
		except ValueError:
			frappe.throw(_("Row name column(s) must be column letters, such as A, or B, C."))
		self.label_columns = ", ".join(column_letter(c) for c in cfg.label_columns) or "A"

		if not self.is_new():
			self.keep_status_fields()
		ids_given = bool(self.drive_id and self.item_id)
		# IDs typed by hand (when a link can't be resolved) are used as they are.
		ids_typed = ids_given and (self.is_new() or self.has_value_changed("drive_id") or self.has_value_changed("item_id"))
		if self.file_link and not ids_typed and (self.is_new() or self.has_value_changed("file_link") or not ids_given):
			self.resolve_file()
		if cint(self.enabled) and not (self.drive_id and self.item_id):
			frappe.throw(_("Paste the file's link and save, so the app can find the file."))

		if not cint(self.enabled):
			self.status = "Disabled"
		elif self.status in (None, "", "Disabled"):
			self.status = "New"

	def keep_status_fields(self):
		current = frappe.db.get_value(self.doctype, self.name, STATUS_FIELDS, as_dict=True)
		if current:
			for fieldname in STATUS_FIELDS:
				self.set(fieldname, current.get(fieldname))

	def resolve_file(self):
		"""Turn the pasted link into the drive and item IDs Microsoft Graph uses."""
		from hr_services.teams_excel_notify.tasks import get_settings, graph_client

		settings = get_settings()
		if not settings.is_configured():
			frappe.throw(_("Fill in Teams Notify Settings (tenant ID, client ID and client secret) first."))
		try:
			item = graph_client(settings).resolve_link(self.file_link)
		except GraphError as exc:
			frappe.throw(str(exc), title=_("Could not open the file"))
		drive_id = (item.get("parentReference") or {}).get("driveId")
		if not drive_id:
			frappe.throw(_("Microsoft did not return the file's drive. Copy the link again and save."))
		self.drive_id = drive_id
		self.item_id = item["id"]
		self.file_name = item.get("name")
		self.file_web_url = item.get("webUrl")

	def on_update(self):
		if self.flags.in_insert:
			return
		if any(self.has_value_changed(fieldname) for fieldname in RESET_FIELDS):
			from hr_services.teams_excel_notify.tasks import reset_state

			reset_state(self.name, status="New" if cint(self.enabled) else "Disabled")

	def on_trash(self):
		from hr_services.teams_excel_notify.tasks import LOG, STATE

		for doctype in (STATE, LOG):
			for name in frappe.get_all(doctype, filters={"watch": self.name}, pluck="name"):
				frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
