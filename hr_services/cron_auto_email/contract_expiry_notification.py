# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Contract expiry notifications for project employees (Misk by default).

Runs daily at 09:00 Asia/Riyadh via the cron entry in hooks.py. For every active
employee on the configured project whose contract_end_date falls inside the notice
window, one email is sent to the configured recipients.

Rules:
  * first email as soon as the contract is within `notice_days`
  * then a reminder every `repeat_after_days` days while it stays in the window
  * a change to contract_end_date starts a fresh cycle and notifies immediately
  * extending the date beyond the window drops the employee out, so reminders stop

Every attempt is recorded in `Contract Expiry Notification Log`, which is also the
state used for the rules above. Everything configurable lives in
`Contract Expiry Notification Settings`.
"""

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	cint,
	date_diff,
	formatdate,
	get_link_to_form,
	getdate,
	now_datetime,
	nowdate,
)

from hr_services.permissions.notification_access import throw_unless_administrator

SETTINGS_DOCTYPE = "Contract Expiry Notification Settings"
LOG_DOCTYPE = "Contract Expiry Notification Log"

# Used when the corresponding Settings field is blank, so a cleared template can
# never result in an empty email going out.
DEFAULT_SUBJECT = (
	"Contract Expiry Alert: {{ employee_name }} ({{ employee }}) - {{ days_remaining }} days remaining"
)
DEFAULT_EXPIRED_SUBJECT = (
	"Contract EXPIRED: {{ employee_name }} ({{ employee }}) - {{ days_overdue }} days overdue"
)
DEFAULT_MESSAGE = """<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f272e;">
	{% if is_overdue %}
	<div style="background:#fbeaea;border-left:4px solid #c0392b;padding:10px 14px;margin-bottom:16px;">
		<b>Contract already expired.</b> This contract ended on {{ contract_end_date }},
		{{ days_overdue }} day(s) ago.
	</div>
	{% else %}
	<div style="background:#fdf5e3;border-left:4px solid #e0a800;padding:10px 14px;margin-bottom:16px;">
		<b>Contract expiring soon.</b> {{ days_remaining }} day(s) remaining, ending {{ contract_end_date }}.
	</div>
	{% endif %}
	<p>Please review and take the required action for the following employee.</p>
	<table cellpadding="8" cellspacing="0" border="0"
		style="border-collapse:collapse;width:100%;max-width:640px;">
		<tr style="background:#f4f5f6;"><td width="40%"><b>Employee ID</b></td><td>{{ employee }}</td></tr>
		<tr><td><b>Employee Name</b></td><td>{{ employee_name }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Designation</b></td><td>{{ designation or "-" }}</td></tr>
		<tr><td><b>Department</b></td><td>{{ department or "-" }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Project</b></td><td>{{ project_name or project }}</td></tr>
		<tr><td><b>Date of Joining</b></td><td>{{ date_of_joining or "-" }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Contract End Date</b></td><td><b>{{ contract_end_date }}</b></td></tr>
		<tr><td><b>Days Remaining</b></td><td>{{ days_remaining }}</td></tr>
	</table>
	<p style="margin-top:18px;">{{ employee_link }}</p>
</div>"""


def apply_template_defaults(settings):
	"""Fill blank template fields with the built-in defaults (in memory)."""
	if not (settings.subject_template or "").strip():
		settings.subject_template = DEFAULT_SUBJECT

	if not (settings.expired_subject_template or "").strip():
		settings.expired_subject_template = DEFAULT_EXPIRED_SUBJECT

	if not (settings.message_template or "").strip():
		settings.message_template = DEFAULT_MESSAGE

	return settings


def get_settings():
	"""Settings Single with defaults applied for any field left blank."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	apply_template_defaults(settings)

	if not settings.notice_days:
		settings.notice_days = 95

	if not settings.repeat_after_days:
		settings.repeat_after_days = 3

	return settings


def parse_recipients(raw):
	"""Split the Small Text field into a clean list of addresses."""
	if not raw:
		return []

	parts = (raw or "").replace(",", "\n").replace(";", "\n").split("\n")
	seen, recipients = set(), []

	for part in parts:
		email = part.strip()
		if email and email.lower() not in seen:
			seen.add(email.lower())
			recipients.append(email)

	return recipients


def check_enabled(settings):
	"""Return (enabled, reason). Reason explains why it is off, for the preview."""
	if not settings.enabled:
		return False, _("Notifications are disabled in Contract Expiry Notification Settings.")

	restrict_to = (settings.restrict_to_site or "").strip()
	if restrict_to and restrict_to != frappe.local.site:
		return False, _("This site ({0}) is not the designated sending site ({1}).").format(
			frappe.local.site, restrict_to
		)

	if not settings.project:
		return False, _("No project is configured.")

	if not parse_recipients(settings.recipients):
		return False, _("No recipients are configured.")

	return True, ""


def get_due_employees(settings):
	"""Active employees on the project whose contract is inside the notice window."""
	today = getdate(nowdate())
	window_end = add_days(today, cint(settings.notice_days))

	filters = {
		"project": settings.project,
		"status": "Active",
		"contract_end_date": ["<=", window_end],
	}

	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=[
			"name",
			"employee_name",
			"designation",
			"department",
			"project",
			"project_name",
			"date_of_joining",
			"contract_end_date",
		],
		order_by="contract_end_date asc",
	)

	# `contract_end_date <= window_end` also matches NULL in some MariaDB modes,
	# so drop unset dates explicitly rather than relying on the filter.
	employees = [emp for emp in employees if emp.contract_end_date]

	if not settings.include_expired:
		employees = [emp for emp in employees if getdate(emp.contract_end_date) >= today]

	return employees


def get_last_log(employee):
	"""Most recent successful notification for this employee, or None.

	Failed attempts are ignored so the next run retries them.
	"""
	rows = frappe.get_all(
		LOG_DOCTYPE,
		filters={"employee": employee, "status": "Sent"},
		fields=["name", "contract_end_date", "notified_on"],
		order_by="notified_on desc",
		limit=1,
	)

	return rows[0] if rows else None


def should_notify(employee, contract_end_date, repeat_after_days):
	"""Return (send, reason). Reason is shown in the preview dialog."""
	last = get_last_log(employee)

	if not last:
		return True, _("First notification")

	if getdate(last.contract_end_date) != getdate(contract_end_date):
		return True, _("Contract end date changed from {0}").format(formatdate(last.contract_end_date))

	days_since = date_diff(getdate(nowdate()), getdate(last.notified_on))
	if days_since >= cint(repeat_after_days):
		return True, _("Reminder, last sent {0} day(s) ago").format(days_since)

	return False, _("Already notified {0} day(s) ago").format(days_since)


def build_context(emp, days_remaining):
	is_overdue = days_remaining < 0

	return {
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"designation": emp.designation,
		"department": emp.department,
		"project": emp.project,
		"project_name": emp.project_name,
		"date_of_joining": formatdate(emp.date_of_joining) if emp.date_of_joining else "",
		"contract_end_date": formatdate(emp.contract_end_date),
		"days_remaining": days_remaining,
		"days_overdue": abs(days_remaining) if is_overdue else 0,
		"is_overdue": is_overdue,
		"employee_link": get_link_to_form("Employee", emp.name, _("Open Employee Record")),
	}


def write_log(emp, days_remaining, recipients, subject, status, error_message=None):
	log = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"project": emp.project,
			"contract_end_date": emp.contract_end_date,
			"days_remaining": days_remaining,
			"notified_on": now_datetime(),
			"recipients": ", ".join(recipients),
			"subject": subject,
			"status": status,
			"error_message": error_message,
		}
	)
	log.insert(ignore_permissions=True)

	return log


def notify_employee(emp, settings, recipients):
	"""Render and queue one email, then record the attempt. Returns the log row."""
	days_remaining = date_diff(getdate(emp.contract_end_date), getdate(nowdate()))
	context = build_context(emp, days_remaining)

	subject_template = (
		settings.expired_subject_template if context["is_overdue"] else settings.subject_template
	)
	subject = frappe.render_template(subject_template, context)
	message = frappe.render_template(settings.message_template, context)

	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=message,
		reference_doctype="Employee",
		reference_name=emp.name,
		now=False,
	)

	return write_log(emp, days_remaining, recipients, subject, "Sent")


def send_contract_expiry_notifications():
	"""Scheduled entry point. Wired to cron "0 9 * * *" in hooks.py."""
	settings = get_settings()
	enabled, reason = check_enabled(settings)

	if not enabled:
		return {"enabled": False, "reason": reason, "sent": 0, "skipped": 0, "failed": 0}

	recipients = parse_recipients(settings.recipients)
	sent = skipped = failed = 0

	for emp in get_due_employees(settings):
		try:
			send, _reason = should_notify(emp.name, emp.contract_end_date, settings.repeat_after_days)

			if not send:
				skipped += 1
				continue

			notify_employee(emp, settings, recipients)
			frappe.db.commit()
			sent += 1

		except Exception:
			# One bad record must not stop the rest of the run.
			frappe.db.rollback()
			failed += 1
			traceback = frappe.get_traceback()

			try:
				days_remaining = date_diff(getdate(emp.contract_end_date), getdate(nowdate()))
				write_log(emp, days_remaining, recipients, "", "Failed", traceback[-1000:])
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()

			frappe.log_error(
				title=f"Contract expiry notification failed: {emp.name}",
				message=traceback,
			)

	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_run_on", now_datetime())
	frappe.db.commit()

	return {"enabled": True, "sent": sent, "skipped": skipped, "failed": failed}


@frappe.whitelist()
def preview_contract_expiry_notifications():
	"""Show who would be emailed right now. Sends nothing."""
	throw_unless_administrator()

	settings = get_settings()
	enabled, reason = check_enabled(settings)
	recipients = parse_recipients(settings.recipients)

	if not enabled:
		return {"enabled": False, "reason": reason, "recipients": recipients, "employees": []}

	today = getdate(nowdate())
	due = []

	for emp in get_due_employees(settings):
		send, why = should_notify(emp.name, emp.contract_end_date, settings.repeat_after_days)

		if not send:
			continue

		days_remaining = date_diff(getdate(emp.contract_end_date), today)
		due.append(
			{
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"contract_end_date": formatdate(emp.contract_end_date),
				"days_remaining": days_remaining,
				"is_overdue": days_remaining < 0,
				"reason": why,
			}
		)

	return {"enabled": True, "reason": "", "recipients": recipients, "employees": due}


@frappe.whitelist()
def send_contract_expiry_notifications_now():
	"""Manual trigger from the Settings form. Dedup rules still apply."""
	throw_unless_administrator()

	return send_contract_expiry_notifications()
