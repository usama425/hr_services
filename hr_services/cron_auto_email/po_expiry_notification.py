# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""PO running-low notifications for project employees (Misk full-time by default).

Runs daily at 09:00 Asia/Riyadh via the cron entry in hooks.py. Every morning it
looks at submitted, Active `PO Management` records belonging to active employees on
the configured project and employment type, and emails the configured recipients
about any PO whose `remaining_units` has dropped to the threshold or below.

Rules:
  * one email per PO record, ever - there is no repeat reminder
  * a new PO for the same employee is a different record, so it gets its own
    single alert once it runs low
  * a PO already emailed is never emailed again, even if units change

Every attempt is recorded in `PO Expiry Notification Log`, which is also the state
that enforces "one PO, one email". Everything configurable lives in
`PO Expiry Notification Settings`.
"""

import frappe
from frappe import _
from frappe.utils import flt, get_link_to_form, now_datetime

from hr_services.permissions.notification_access import throw_unless_administrator

SETTINGS_DOCTYPE = "PO Expiry Notification Settings"
LOG_DOCTYPE = "PO Expiry Notification Log"
PO_DOCTYPE = "PO Management"

# Used when the corresponding Settings field is blank, so a cleared template can
# never result in an empty email going out.
DEFAULT_SUBJECT = (
	"PO Running Low: {{ employee_name }} ({{ employee }}) - {{ remaining_units }} units remaining on {{ po_no }}"
)
DEFAULT_EXHAUSTED_SUBJECT = (
	"PO Fully Consumed: {{ employee_name }} ({{ employee }}) - {{ po_no }} has no units left"
)
DEFAULT_MESSAGE = """<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f272e;">
	{% if is_exhausted %}
	<div style="background:#fbeaea;border-left:4px solid #c0392b;padding:10px 14px;margin-bottom:16px;">
		<b>Purchase order fully consumed.</b> PO {{ po_no }} has {{ remaining_units }} units left
		out of {{ po_units }}.
	</div>
	{% else %}
	<div style="background:#fdf5e3;border-left:4px solid #e0a800;padding:10px 14px;margin-bottom:16px;">
		<b>Purchase order running low.</b> PO {{ po_no }} has only {{ remaining_units }} units left
		out of {{ po_units }}.
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
		<tr style="background:#f4f5f6;"><td><b>PO No</b></td><td><b>{{ po_no }}</b></td></tr>
		<tr><td><b>PO Type</b></td><td>{{ po_type or "-" }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>PO Units</b></td><td>{{ po_units }}</td></tr>
		<tr><td><b>Used Units</b></td><td>{{ used_units }}</td></tr>
		<tr style="background:#f4f5f6;"><td><b>Remaining Units</b></td><td><b>{{ remaining_units }}</b></td></tr>
	</table>
	<p style="margin-top:18px;">{{ po_link }} &nbsp;|&nbsp; {{ employee_link }}</p>
</div>"""


def apply_template_defaults(settings):
	"""Fill blank template fields with the built-in defaults (in memory)."""
	if not (settings.subject_template or "").strip():
		settings.subject_template = DEFAULT_SUBJECT

	if not (settings.exhausted_subject_template or "").strip():
		settings.exhausted_subject_template = DEFAULT_EXHAUSTED_SUBJECT

	if not (settings.message_template or "").strip():
		settings.message_template = DEFAULT_MESSAGE

	return settings


def get_settings():
	"""Settings Single with defaults applied for any field left blank."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)
	apply_template_defaults(settings)

	if not settings.remaining_units_threshold:
		settings.remaining_units_threshold = 95

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

	if not parse_recipients(settings.recipients):
		return False, _("No recipients are configured.")

	return True, ""


def get_low_pos(settings):
	"""Submitted Active POs at or below the threshold, for employees in scope.

	POs with no `remaining_units` recorded are skipped: a blank value says nothing
	about how much is left, and guessing would raise a false alarm.
	"""
	conditions = [
		"p.docstatus = 1",
		"p.status = 'Active'",
		"e.status = 'Active'",
		"e.project = %(project)s",
		"p.remaining_units is not null",
		"p.remaining_units <= %(threshold)s",
	]
	values = {
		"project": settings.project,
		"threshold": flt(settings.remaining_units_threshold),
	}

	if settings.employment_type:
		conditions.append("e.employment_type = %(employment_type)s")
		values["employment_type"] = settings.employment_type

	return frappe.db.sql(
		"""
		select
			p.name as po, p.po_no, p.po_type, p.po_units, p.used_units, p.remaining_units,
			e.name as employee, e.employee_name, e.designation, e.department,
			e.employment_type, e.project, e.project_name
		from `tabPO Management` p
		inner join `tabEmployee` e on e.name = p.employee_no
		where {conditions}
		order by p.remaining_units asc
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)


def already_notified(po):
	"""True when this PO record has already had a successful alert.

	Failed attempts do not count, so the next run retries them.
	"""
	return bool(
		frappe.db.exists(LOG_DOCTYPE, {"po": po, "status": "Sent"})
	)


def build_context(row):
	is_exhausted = flt(row.remaining_units) <= 0

	return {
		"employee": row.employee,
		"employee_name": row.employee_name,
		"designation": row.designation,
		"department": row.department,
		"employment_type": row.employment_type,
		"project": row.project,
		"project_name": row.project_name,
		"po": row.po,
		"po_no": row.po_no,
		"po_type": row.po_type,
		"po_units": flt(row.po_units),
		"used_units": flt(row.used_units),
		"remaining_units": flt(row.remaining_units),
		"is_exhausted": is_exhausted,
		"employee_link": get_link_to_form("Employee", row.employee, _("Open Employee Record")),
		"po_link": get_link_to_form(PO_DOCTYPE, row.po, _("Open PO Record")),
	}


def write_log(row, recipients, cc, subject, status, error_message=None):
	log = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"po": row.po,
			"po_no": row.po_no,
			"po_type": row.po_type,
			"po_units": row.po_units,
			"used_units": row.used_units,
			"remaining_units": row.remaining_units,
			"employee": row.employee,
			"employee_name": row.employee_name,
			"project": row.project,
			"employment_type": row.employment_type,
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


def notify_po(row, settings, recipients, cc):
	"""Render and queue one email for this PO, then log it."""
	context = build_context(row)

	subject_template = (
		settings.exhausted_subject_template if context["is_exhausted"] else settings.subject_template
	)
	subject = frappe.render_template(subject_template, context)
	message = frappe.render_template(settings.message_template, context)

	frappe.sendmail(
		recipients=recipients,
		cc=cc,
		subject=subject,
		message=message,
		reference_doctype="Employee",
		reference_name=row.employee,
		now=False,
	)

	return write_log(row, recipients, cc, subject, "Sent")


def send_po_expiry_notifications():
	"""Scheduled entry point. Wired to cron "0 9 * * *" in hooks.py."""
	settings = get_settings()
	enabled, reason = check_enabled(settings)

	if not enabled:
		return {"enabled": False, "reason": reason, "sent": 0, "skipped": 0, "failed": 0}

	recipients = parse_recipients(settings.recipients)
	cc = parse_recipients(settings.cc_recipients)
	sent = skipped = failed = 0

	for row in get_low_pos(settings):
		try:
			if already_notified(row.po):
				skipped += 1
				continue

			notify_po(row, settings, recipients, cc)
			frappe.db.commit()
			sent += 1

		except Exception:
			# One bad record must not stop the rest of the run.
			frappe.db.rollback()
			failed += 1
			traceback = frappe.get_traceback()

			try:
				write_log(row, recipients, cc, "", "Failed", traceback[-1000:])
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()

			frappe.log_error(
				title=f"PO expiry notification failed: {row.po}",
				message=traceback,
			)

	frappe.db.set_single_value(SETTINGS_DOCTYPE, "last_run_on", now_datetime())
	frappe.db.commit()

	return {"enabled": True, "sent": sent, "skipped": skipped, "failed": failed}


@frappe.whitelist()
def preview_po_expiry_notifications():
	"""Show which POs would be emailed right now. Sends nothing."""
	throw_unless_administrator()

	settings = get_settings()
	enabled, reason = check_enabled(settings)
	recipients = parse_recipients(settings.recipients)
	cc = parse_recipients(settings.cc_recipients)

	base = {
		"recipients": recipients,
		"cc": cc,
		"threshold": flt(settings.remaining_units_threshold),
		"pos": [],
	}

	if not enabled:
		return dict(base, enabled=False, reason=reason)

	low = get_low_pos(settings)
	due = [
		{
			"po": row.po,
			"po_no": row.po_no,
			"employee": row.employee,
			"employee_name": row.employee_name,
			"po_units": flt(row.po_units),
			"used_units": flt(row.used_units),
			"remaining_units": flt(row.remaining_units),
			"is_exhausted": flt(row.remaining_units) <= 0,
		}
		for row in low
		if not already_notified(row.po)
	]

	reason = ""
	if low and not due:
		reason = _("All {0} PO(s) below the threshold have already been notified.").format(len(low))
	elif not low:
		reason = _("No PO is at or below {0} remaining units.").format(
			flt(settings.remaining_units_threshold)
		)

	return dict(base, enabled=True, reason=reason, pos=due)


@frappe.whitelist()
def send_po_expiry_notifications_now():
	"""Manual trigger from the Settings form. One-email-per-PO still applies."""
	throw_unless_administrator()

	return send_po_expiry_notifications()
