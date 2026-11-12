"""Scheduled maintenance: a countdown banner before it starts, then a locked site.

Configure per site in site_config.json (times are in the site's time zone):

    bench --site <site> set-config -p scheduled_maintenance '{"start": "2026-11-12 18:00:00", "message": "..."}'

Before `start`, every desk page and the login page show a black banner at the top
with `message` and a live countdown. From `start` onwards every request gets the
"Please wait..." page (hr_services/templates/expiry_notice.html) and nobody can log
in or use the API. It stays that way until the setting is removed:

    bench --site <site> set-config -p scheduled_maintenance None
"""

import frappe
from frappe.utils import get_datetime, now_datetime


def get_schedule():
	conf = (frappe.local.conf or {}).get("scheduled_maintenance")
	if not isinstance(conf, dict) or not conf.get("start"):
		return None
	try:
		return frappe._dict(start=get_datetime(conf["start"]), message=conf.get("message") or "")
	except Exception:
		return None


def is_locked():
	schedule = get_schedule()
	return bool(schedule and now_datetime() >= schedule.start)


def before_request():
	"""Block every request once the maintenance start time has passed."""
	if is_locked():
		raise frappe.SessionStopped("Scheduled maintenance")


@frappe.whitelist(allow_guest=True)
def banner():
	"""What the banner script shows: the message and seconds left until the start."""
	schedule = get_schedule()
	if not schedule:
		return None
	seconds_left = int((schedule.start - now_datetime()).total_seconds())
	if seconds_left <= 0:
		return None
	return {"message": schedule.message, "seconds_left": seconds_left}
