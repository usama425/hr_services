# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

TERMINAL_STATUSES = ("Completed", "Cancelled")


class ERCTask(Document):
	def before_insert(self):
		if not self.assigned_by:
			self.assigned_by = frappe.session.user

	def validate(self):
		self.validate_assignees()
		self.set_assignee_names()
		self.stamp_checklist()
		self.set_progress()
		self.sync_status_with_checklist()
		self.set_completion_stamps()
		self.record_reassignment()

	# --- assignees ------------------------------------------------------------

	def get_assignees(self):
		"""The users responsible for this task, de-duplicated, order preserved."""
		seen, users = set(), []

		for row in self.assignees or []:
			if row.user and row.user not in seen:
				seen.add(row.user)
				users.append(row.user)

		return users

	def validate_assignees(self):
		users = self.get_assignees()

		if not users:
			frappe.throw(_("Assign this task to at least one person."))

		# Collapse any duplicate rows the picker may have allowed.
		if len(users) != len(self.assignees or []):
			self.set("assignees", [])
			for user in users:
				self.append("assignees", {"user": user})

	def set_assignee_names(self):
		"""Readable list for the list view and reports."""
		names = [
			frappe.db.get_value("User", user, "full_name") or user for user in self.get_assignees()
		]
		self.assignee_names = ", ".join(names)

	def on_update(self):
		self.notify_on_completion()

	# --- checklist ------------------------------------------------------------

	def stamp_checklist(self):
		"""Record who ticked each item, and clear the stamp if it is unticked again."""
		previous = {}
		if not self.is_new():
			previous = {
				row.name: row.completed
				for row in (self.get_doc_before_save() or self).get("checklist") or []
			}

		for row in self.checklist or []:
			was_done = cint(previous.get(row.name, 0))

			if cint(row.completed) and not was_done:
				row.completed_by = frappe.session.user
				row.completed_on = now_datetime()
			elif not cint(row.completed):
				row.completed_by = None
				row.completed_on = None

	def set_progress(self):
		total = len(self.checklist or [])

		if not total:
			# No checklist: progress mirrors the status rather than sitting at zero.
			self.progress = 100 if self.status == "Completed" else 0
			return

		done = sum(1 for row in self.checklist if cint(row.completed))
		self.progress = flt(done * 100 / total, 2)

	def sync_status_with_checklist(self):
		"""Ticking the last item completes the task; unticking one reopens it.

		A cancelled task is left alone - cancelling is a deliberate act and the
		checklist should not drag it back open.
		"""
		if self.status == "Cancelled" or not self.checklist:
			return

		all_done = all(cint(row.completed) for row in self.checklist)

		if all_done and self.status != "Completed":
			self.status = "Completed"
		elif not all_done and self.status == "Completed":
			self.status = "In Progress"

	def set_completion_stamps(self):
		if self.status == "Completed":
			if not self.completed_on:
				self.completed_on = now_datetime()
			if not self.completed_by:
				self.completed_by = frappe.session.user
		else:
			self.completed_on = None
			self.completed_by = None

	# --- reassignment ---------------------------------------------------------

	def record_reassignment(self):
		"""Log any change to the assignee list, in or out."""
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before:
			return

		was = [row.user for row in (before.get("assignees") or []) if row.user]
		now = self.get_assignees()

		if set(was) == set(now):
			return

		self.append(
			"reassignments",
			{
				"from_assignees": ", ".join(was),
				"to_assignees": ", ".join(now),
				"reassigned_by": frappe.session.user,
				"reassigned_on": now_datetime(),
				"reason": self.flags.reassign_reason or "",
			},
		)

	# --- notification ---------------------------------------------------------

	def notify_on_completion(self):
		before = self.get_doc_before_save()

		if not before or before.status == self.status or self.status != "Completed":
			return

		from hr_services.cron_auto_email.task_notifications import send_completion_email

		send_completion_email(self)


@frappe.whitelist()
def reassign(task, assignees, reason=None):
	"""Replace the assignee list in one step, so the trail records a single change."""
	if isinstance(assignees, str):
		assignees = frappe.parse_json(assignees)

	assignees = [u for u in (assignees or []) if u]

	if not assignees:
		frappe.throw(_("Assign this task to at least one person."))

	doc = frappe.get_doc("ERC Task", task)
	doc.check_permission("write")

	doc.set("assignees", [])
	for user in assignees:
		doc.append("assignees", {"user": user})

	doc.flags.reassign_reason = reason or ""
	doc.save()

	return doc.name


@frappe.whitelist()
def toggle_checklist_item(task, row_name, completed):
	"""Tick or untick one checklist row without opening the whole form."""
	doc = frappe.get_doc("ERC Task", task)
	doc.check_permission("write")

	for row in doc.checklist:
		if row.name == row_name:
			row.completed = cint(completed)
			break
	else:
		frappe.throw(_("Checklist item not found."))

	doc.save()

	return {"status": doc.status, "progress": doc.progress}
