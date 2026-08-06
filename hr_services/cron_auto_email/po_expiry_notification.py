# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""PO expiry notifications for project employees (Misk full-time by default).

Runs daily at 09:00 Asia/Riyadh via the cron entry in hooks.py. Unlike contract
expiry, the PO expiry date is a single date shared by everybody in scope (31
December), so the window test is on that one date and either everybody is due or
nobody is.

Rules:
  * first email once the PO expiry date is within `notice_days`
  * then a reminder every `repeat_after_days` days while it stays in the window
  * `roll_forward_annually` moves the date to the next year once it has passed,
    which also counts as a date change and starts a fresh cycle

Every attempt is recorded in `PO Expiry Notification Log`, which is also the state
used for the rules above. Everything configurable lives in
`PO Expiry Notification Settings`.
"""

import calendar
import datetime

import frappe
from frappe import _
from frappe.utils import (
	cint,
	date_diff,
	formatdate,
	get_link_to_form,
	getdate,
	now_datetime,
	nowdate,
)

from hr_services.permissions.notification_access import throw_unless_administrator

SETTINGS_DOCTYPE = "PO Expiry Notification Settings"
LOG_DOCTYPE = "PO Expiry Notification Log"

# Used when the corresponding Settings field is blank, so a cleared template can
# never result in an empty email going out.
DEFAULT_SUBJECT = (
	"PO Expiry Alert: {{ employee_name }} ({{ employee }}) - {{ days_remaining }} days remaining"
)
DEFAULT_EXPIRED_SUBJECT = (
	"PO EXPIRED: {{ employee_name }} ({{ employee }}) - {{ days_overdue }} days overdue"
)
DEFAULT_MESSAGE = """<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f272e;">
	{% if is_overdue %}
	<div style="background:#fbeaea;border-left:4px solid #c0392b;padding:10px 14px;margin-bottom:16px;">
		<b>Purchase order already expired.</b> The PO covering this employee expired on
		{{ po_expiry_date }}, {{ days_overdue }} day(s) ago.
	</div>
	{% else %}
	<div style="background:#fdf5e3;border-left:4px solid #e0a800;padding:10px 14px;margin-bottom:16px;">
		<b>Purchase order expiring soon.</b> The PO covering this employee expires on
		{{ po_expiry_date }}, {{ days_remaining }} day(s) from now.
	</div>
	{% endif %}
	<p>Please arrange the renewal for the following employee.</p>
	<table cellpadding="8" cellspacing="0" border="0"
		style="border-collapse:collapse;width:100%;max-width:640px;">
		<tr style="background:#f4f5f6;"><td width="40%"><b>Employee ID</b></td><td>{{ employee }}</td></tr>
		<tr><td><b>Employee Name</b></td><td>{{ employee_name }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Designation</b></td><td>{{ designation or "-" }}</td></tr>
		<tr><td><b>Department</b></td><td>{{ department or "-" }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Employment Type</b></td><td>{{ employment_type or "-" }}</td></tr>
		<tr><td><b>Project</b></td><td>{{ project_name or project }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Date of Joining</b></td><td>{{ date_of_joining or "-" }}</td></tr>
		<tr><td><b>PO Expiry Date</b></td><td><b>{{ po_expiry_date }}</b></td></tr>
		<tr style="background:#f4f5f6;"><td><b>Days Remaining</b></td><td>{{ days_remaining }}</td></tr>
	</table>
	<p style="margin-top:18px;">{{ employee_link }}</p>
	<p style="color:#8d99a6;font-size:12px;margin-top:24px;">
		Automatic notification from ERP. To change recipients or this template, open
		PO Expiry Notification Settings.
	</p>
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
	"""Split a Small Text field into a clean list of addresses."""
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


def _on_year(month, day, year):
	"""Same month/day in `year`, clamped for short months (e.g. 29 Feb -> 28 Feb)."""
	return datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def get_effective_expiry_date(settings):
	"""The PO expiry date to work against today.

	With `roll_forward_annually` on, the stored day/month is projected onto the next
	year once this year's date has passed, so 31 Dec 2026 becomes 31 Dec 2027 on
	1 Jan 2027 without anyone editing the settings.
	"""
	stored = getdate(settings.po_expiry_date)

	if not settings.roll_forward_annually:
		return stored

	today = getdate(nowdate())
	candidate = _on_year(stored.month, stored.day, today.year)

	if candidate < today:
		candidate = _on_year(stored.month, stored.day, today.year + 1)

	return candidate


def check_enabled(settings):
	"""Return (enabled, reason). Reason explains why it is off, for the preview."""
	if not settings.enabled:
		return False, _("Notifications are disabled in PO Expiry Notification Settings.")

	restrict_to = (settings.restrict_to_site or "").strip()
	if restrict_to and restrict_to != frappe.local.site:
		return False, _("This site ({0}) is not the designated sending site ({1}).").format(
			frappe.local.site, restrict_to
		)

	if not settings.project:
		return False, _("No project is configured.")

	if not settings.po_expiry_date:
		return False, _("No PO expiry date is configured.")

	if not parse_recipients(settings.recipients):
		return False, _("No recipients are configured.")

	return True, ""


def get_due_employees(settings, ignore_window=False):
	"""Employees in scope, or [] when the shared expiry date is outside the window.

	The PO expiry date is the same for everyone, so the window test applies to the
	whole group at once. `ignore_window` is only ever set by the manual trigger.
	"""
	expiry_date = get_effective_expiry_date(settings)
	days_remaining = date_diff(expiry_date, getdate(nowdate()))

	if not ignore_window and days_remaining > cint(settings.notice_days):
		return []

	filters = {"project": settings.project, "status": "Active"}

	if settings.employment_type:
		filters["employment_type"] = settings.employment_type

	return frappe.get_all(
		"Employee",
		filters=filters,
		fields=[
			"name",
			"employee_name",
			"designation",
			"department",
			"employment_type",
			"project",
			"project_name",
			"date_of_joining",
		],
		order_by="employee_name asc",
	)


def get_last_log(employee):
	"""Most recent successful notification for this employee, or None.

	Failed attempts are ignored so the next run retries them.
	"""
	rows = frappe.get_all(
		LOG_DOCTYPE,
		filters={"employee": employee, "status": "Sent"},
		fields=["name", "po_expiry_date", "notified_on"],
		order_by="notified_on desc",
		limit=1,
	)

	return rows[0] if rows else None


def should_notify(employee, expiry_date, repeat_after_days):
	"""Return (send, reason). Reason is shown in the preview dialog."""
	last = get_last_log(employee)

	if not last:
		return True, _("First notification")

	if getdate(last.po_expiry_date) != getdate(expiry_date):
		return True, _("PO expiry date changed from {0}").format(formatdate(last.po_expiry_date))

	days_since = date_diff(getdate(nowdate()), getdate(last.notified_on))
	if days_since >= cint(repeat_after_days):
		return True, _("Reminder, last sent {0} day(s) ago").format(days_since)

	return False, _("Already notified {0} day(s) ago").format(days_since)


def build_context(emp, expiry_date, days_remaining):
	is_overdue = days_remaining < 0

	return {
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"designation": emp.designation,
		"department": emp.department,
		"employment_type": emp.employment_type,
		"project": emp.project,
		"project_name": emp.project_name,
		"date_of_joining": formatdate(emp.date_of_joining) if emp.date_of_joining else "",
		"po_expiry_date": formatdate(expiry_date),
		"days_remaining": days_remaining,
		"days_overdue": abs(days_remaining) if is_overdue else 0,
		"is_overdue": is_overdue,
		"employee_link": get_link_to_form("Employee", emp.name, _("Open Employee Record")),
	}


def write_log(emp, expiry_date, days_remaining, recipients, cc, subject, status, error_message=None):
	log = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"employee": emp.name,
			"employee_name": emp.employee_name,
			"project": emp.project,
			"employment_type": emp.employment_type,
			"po_expiry_date": expiry_date,
			"days_remaining": days_remaining,
			"notified_on": now_datetime(),
			"recipients": ", ".join(recipients),
			"cc_recipients": ", ".join(cc),
			"subject": subject,
			"status": status,
			"error_message": error_message,
		}
	)
	log.insert(ignore_permissions=True)

	return log


def notify_employee(emp, settings, expiry_date, recipients, cc):
	"""Render and queue one email to all recipients with the CC, then log it."""
	days_remaining = date_diff(getdate(expiry_date), getdate(nowdate()))
	context = build_context(emp, expiry_date, days_remaining)

	subject_template = (
		settings.expired_subject_template if context["is_overdue"] else settings.subject_template
	)
	subject = frappe.render_template(subject_template, context)
	message = frappe.render_template(settings.message_template, context)

	frappe.sendmail(
		recipients=recipients,
		cc=cc,
		subject=subject,
		message=message,
		reference_doctype="Employee",
		reference_name=emp.name,
		now=False,
	)

	return write_log(emp, expiry_date, days_remaining, recipients, cc, subject, "Sent")


def send_po_expiry_notifications(ignore_window=False):
	"""Scheduled entry point. Wired to cron "0 9 * * *" in hooks.py.

	The scheduler never passes `ignore_window`, so the notice window is always
	honoured on the automatic run.
	"""
	settings = get_settings()
	enabled, reason = check_enabled(settings)

	if not enabled:
		return {"enabled": False, "reason": reason, "sent": 0, "skipped": 0, "failed": 0}

	expiry_date = get_effective_expiry_date(settings)
	recipients = parse_recipients(settings.recipients)
	cc = parse_recipients(settings.cc_recipients)
	sent = skipped = failed = 0

	for emp in get_due_employees(settings, ignore_window=ignore_window):
		try:
			send, _reason = should_notify(emp.name, expiry_date, settings.repeat_after_days)

			if not send:
				skipped += 1
				continue

			notify_employee(emp, settings, expiry_date, recipients, cc)
			frappe.db.commit()
			sent += 1

		except Exception:
			# One bad record must not stop the rest of the run.
			frappe.db.rollback()
			failed += 1
			traceback = frappe.get_traceback()

			try:
				days_remaining = date_diff(getdate(expiry_date), getdate(nowdate()))
				write_log(
					emp, expiry_date, days_remaining, recipients, cc, "", "Failed", traceback[-1000:]
				)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()

			frappe.log_error(
				title=f"PO expiry notification failed: {emp.name}",
				message=traceback,
			)

	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_run_on", now_datetime())
	frappe.db.commit()

	return {"enabled": True, "sent": sent, "skipped": skipped, "failed": failed}


@frappe.whitelist()
def preview_po_expiry_notifications(ignore_window=0):
	"""Show who would be emailed right now. Sends nothing."""
	throw_unless_administrator()

	settings = get_settings()
	enabled, reason = check_enabled(settings)
	recipients = parse_recipients(settings.recipients)
	cc = parse_recipients(settings.cc_recipients)
	expiry_date = get_effective_expiry_date(settings)
	days_remaining = date_diff(expiry_date, getdate(nowdate()))

	base = {
		"recipients": recipients,
		"cc": cc,
		"po_expiry_date": formatdate(expiry_date),
		"days_remaining": days_remaining,
		"employees": [],
	}

	if not enabled:
		return dict(base, enabled=False, reason=reason)

	due = []
	for emp in get_due_employees(settings, ignore_window=cint(ignore_window)):
		send, why = should_notify(emp.name, expiry_date, settings.repeat_after_days)

		if not send:
			continue

		due.append(
			{
				"employee": emp.name,
				"employee_name": emp.employee_name,
				"employment_type": emp.employment_type,
				"days_remaining": days_remaining,
				"is_overdue": days_remaining < 0,
				"reason": why,
			}
		)

	reason = ""
	if not due and not cint(ignore_window) and days_remaining > cint(settings.notice_days):
		reason = _("PO expiry is {0} days away, outside the {1} day notice window.").format(
			days_remaining, cint(settings.notice_days)
		)

	return dict(base, enabled=True, reason=reason, employees=due)


@frappe.whitelist()
def send_po_expiry_notifications_now(ignore_window=0):
	"""Manual trigger from the Settings form. Dedup rules still apply."""
	throw_unless_administrator()

	return send_po_expiry_notifications(ignore_window=cint(ignore_window))
