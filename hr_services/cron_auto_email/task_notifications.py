# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Task emails for ERC Task.

Two scheduled emails, both at 09:00 Asia/Riyadh via the cron entry in hooks.py:

  * due today   - to the assignee, CC the task's CC list
  * early reminder - the same, `remind_before_days` ahead of the due date

Plus one event-driven email when a task is completed, sent to whoever assigned it.
There is deliberately no assignment email and no daily overdue nag.

Each task carries its own sent-on guard, so re-running a job in the same day can
never double-send. Everything configurable lives in `ERC Task Settings`.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, formatdate, get_link_to_form, getdate, now_datetime, nowdate

SETTINGS_DOCTYPE = "ERC Task Settings"
TASK_DOCTYPE = "ERC Task"
OPEN_STATUSES = ("Open", "In Progress")

DEFAULT_DUE_SUBJECT = "Task due today: {{ subject }}"
DEFAULT_REMINDER_SUBJECT = "Task due in {{ days_remaining }} day(s): {{ subject }}"
DEFAULT_COMPLETION_SUBJECT = "Task completed: {{ subject }}"

DEFAULT_MESSAGE = """<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f272e;">
	{% if days_remaining <= 0 %}
	<div style="background:#fbeaea;border-left:4px solid #c0392b;padding:10px 14px;margin-bottom:16px;">
		<b>This task is due today.</b>
	</div>
	{% else %}
	<div style="background:#fdf5e3;border-left:4px solid #e0a800;padding:10px 14px;margin-bottom:16px;">
		<b>This task is due in {{ days_remaining }} day(s).</b>
	</div>
	{% endif %}
	<table cellpadding="8" cellspacing="0" border="0"
		style="border-collapse:collapse;width:100%;max-width:640px;">
		<tr style="background:#f4f5f6;"><td width="35%"><b>Task</b></td><td>{{ subject }}</td></tr>
		<tr><td><b>Assigned To</b></td><td>{{ assigned_to_name or assigned_to }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Assigned By</b></td><td>{{ assigned_by_name or assigned_by }}</td></tr>
		<tr><td><b>Due Date</b></td><td><b>{{ due_date }}</b></td></tr>
		<tr style="background:#f4f5f6;"><td><b>Priority</b></td><td>{{ priority }}</td></tr>
		<tr><td><b>Status</b></td><td>{{ status }}</td></tr>
		{% if checklist_total %}
		<tr style="background:#f4f5f6;"><td><b>Checklist</b></td><td>{{ checklist_done }} of {{ checklist_total }} done</td></tr>
		{% endif %}
		{% if reference_name %}
		<tr><td><b>Related Record</b></td><td>{{ reference_doctype }}: {{ reference_name }}</td></tr>
		{% endif %}
	</table>
	<p style="margin-top:18px;">{{ task_link }}</p>
</div>"""

DEFAULT_COMPLETION_MESSAGE = """<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f272e;">
	<div style="background:#eaf7ef;border-left:4px solid #28a745;padding:10px 14px;margin-bottom:16px;">
		<b>This task has been completed.</b>
	</div>
	<table cellpadding="8" cellspacing="0" border="0"
		style="border-collapse:collapse;width:100%;max-width:640px;">
		<tr style="background:#f4f5f6;"><td width="35%"><b>Task</b></td><td>{{ subject }}</td></tr>
		<tr><td><b>Completed By</b></td><td>{{ completed_by }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Completed On</b></td><td>{{ completed_on }}</td></tr>
		<tr><td><b>Due Date</b></td><td>{{ due_date }}</td></tr>
		{% if reference_name %}
		<tr style="background:#f4f5f6;"><td><b>Related Record</b></td><td>{{ reference_doctype }}: {{ reference_name }}</td></tr>
		{% endif %}
	</table>
	<p style="margin-top:18px;">{{ task_link }}</p>
</div>"""


def apply_template_defaults(settings):
	"""Fill blank template fields with the built-in defaults (in memory)."""
	pairs = (
		("due_subject_template", DEFAULT_DUE_SUBJECT),
		("reminder_subject_template", DEFAULT_REMINDER_SUBJECT),
		("completion_subject_template", DEFAULT_COMPLETION_SUBJECT),
		("message_template", DEFAULT_MESSAGE),
		("completion_message_template", DEFAULT_COMPLETION_MESSAGE),
	)

	for field, default in pairs:
		if not (settings.get(field) or "").strip():
			settings.set(field, default)

	return settings


def get_settings():
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	apply_template_defaults(settings)

	return settings


def check_enabled(settings):
	"""Return (enabled, reason)."""
	if not settings.enabled:
		return False, _("Task notifications are disabled in ERC Task Settings.")

	restrict_to = (settings.restrict_to_site or "").strip()
	if restrict_to and restrict_to != frappe.local.site:
		return False, _("This site ({0}) is not the designated sending site ({1}).").format(
			frappe.local.site, restrict_to
		)

	return True, ""


def get_cc(task):
	return [row.user for row in (task.get("cc_users") or []) if row.user]


def build_context(task):
	total = len(task.get("checklist") or [])
	done = sum(1 for row in (task.get("checklist") or []) if cint(row.completed))

	return {
		"task": task.name,
		"subject": task.subject,
		"assigned_to": task.assigned_to,
		"assigned_to_name": task.assigned_to_name,
		"assigned_by": task.assigned_by,
		"assigned_by_name": frappe.db.get_value("User", task.assigned_by, "full_name")
		if task.assigned_by
		else "",
		"due_date": formatdate(task.due_date),
		"days_remaining": frappe.utils.date_diff(getdate(task.due_date), getdate(nowdate())),
		"priority": task.priority,
		"status": task.status,
		"progress": task.progress,
		"checklist_done": done,
		"checklist_total": total,
		"reference_doctype": task.reference_doctype,
		"reference_name": task.reference_name,
		"completed_by": task.completed_by,
		"completed_on": formatdate(task.completed_on) if task.completed_on else "",
		"task_link": get_link_to_form(TASK_DOCTYPE, task.name, _("Open Task")),
	}


def send_task_email(task, settings, subject_template, message_template, recipients):
	recipients = [r for r in recipients if r]

	if not recipients:
		return False

	context = build_context(task)
	subject = frappe.render_template(subject_template, context)
	message = frappe.render_template(message_template, context)

	frappe.sendmail(
		recipients=recipients,
		cc=get_cc(task),
		subject=subject,
		message=message,
		reference_doctype=TASK_DOCTYPE,
		reference_name=task.name,
		now=False,
	)

	return True


def send_due_and_reminder_emails():
	"""Scheduled entry point. Wired to cron "0 9 * * *" in hooks.py."""
	settings = get_settings()
	enabled, reason = check_enabled(settings)

	if not enabled:
		return {"enabled": False, "reason": reason, "due": 0, "reminders": 0, "failed": 0}

	if not settings.notify_on_due_date:
		return {"enabled": True, "reason": _("Due-date emails are switched off."), "due": 0, "reminders": 0, "failed": 0}

	today = getdate(nowdate())
	due_sent = reminder_sent = failed = 0

	candidates = frappe.get_all(
		TASK_DOCTYPE,
		filters={"status": ["in", OPEN_STATUSES], "due_date": ["is", "set"]},
		fields=["name", "due_date", "remind_before_days", "due_email_sent_on", "reminder_email_sent_on"],
	)

	for row in candidates:
		try:
			due = getdate(row.due_date)
			is_due_today = due == today
			remind_days = cint(row.remind_before_days)
			is_reminder_day = remind_days > 0 and add_days(due, -remind_days) == today

			if not is_due_today and not is_reminder_day:
				continue

			# getdate(None) returns *today*, so an unset guard field would look like
			# "already sent today" and suppress every first send. Check it is set first.
			sent_on = row.due_email_sent_on if is_due_today else row.reminder_email_sent_on
			if sent_on and getdate(sent_on) == today:
				continue

			task = frappe.get_doc(TASK_DOCTYPE, row.name)
			subject_template = (
				settings.due_subject_template if is_due_today else settings.reminder_subject_template
			)

			if send_task_email(
				task, settings, subject_template, settings.message_template, [task.assigned_to]
			):
				field = "due_email_sent_on" if is_due_today else "reminder_email_sent_on"
				frappe.db.set_value(TASK_DOCTYPE, task.name, field, today, update_modified=False)
				frappe.db.commit()

				if is_due_today:
					due_sent += 1
				else:
					reminder_sent += 1

		except Exception:
			frappe.db.rollback()
			failed += 1
			frappe.log_error(
				title=f"Task due notification failed: {row.name}",
				message=frappe.get_traceback(),
			)

	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_run_on", now_datetime())
	frappe.db.commit()

	return {"enabled": True, "reason": "", "due": due_sent, "reminders": reminder_sent, "failed": failed}


def send_completion_email(task):
	"""Called from ERC Task.on_update when a task moves to Completed."""
	try:
		settings = get_settings()
		enabled, _reason = check_enabled(settings)

		if not enabled or not settings.notify_on_completion:
			return False

		if not task.assigned_by or task.assigned_by == frappe.session.user:
			# The person who assigned it just completed it themselves; no need to tell them.
			return False

		return send_task_email(
			task,
			settings,
			settings.completion_subject_template,
			settings.completion_message_template,
			[task.assigned_by],
		)

	except Exception:
		# Never let a notification failure block saving the task.
		frappe.log_error(
			title=f"Task completion notification failed: {task.name}",
			message=frappe.get_traceback(),
		)
		return False


@frappe.whitelist()
def send_task_notifications_now():
	"""Manual trigger from the Settings form."""
	from hr_services.permissions.notification_access import throw_unless_administrator

	throw_unless_administrator()

	return send_due_and_reminder_emails()
