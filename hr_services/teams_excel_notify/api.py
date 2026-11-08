"""Buttons on the forms call these methods (System Manager only)."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime, time_diff_in_seconds
from frappe.utils.scheduler import is_scheduler_disabled

from hr_services.teams_excel_notify.graph import READ_ROLES, GraphError
from hr_services.teams_excel_notify.tasks import (
	LAST_POLL_KEY,
	LOG,
	STATE,
	WATCH,
	deliver_log,
	get_settings,
	graph_client,
	reset_state,
	run_watch,
)
from hr_services.teams_excel_notify.teams_card import TeamsPostError, build_test_card, post_card

ROLE = "System Manager"


@frappe.whitelist()
def test_connection():
	"""Get a token and show which application permissions it carries."""
	frappe.only_for(ROLE)
	settings = get_settings()
	if not settings.is_configured():
		frappe.throw(_("Fill in the tenant ID, client ID and client secret, then save."))
	client = graph_client(settings)
	try:
		roles = client.token_roles(client.get_token(force=True))
	except GraphError as exc:
		frappe.throw(str(exc), title=_("Connection failed"))
	readable = [r for r in roles if r in READ_ROLES]
	can_find_sites = any(r in roles for r in ("Sites.Read.All", "Sites.ReadWrite.All", "Sites.Selected", "Sites.FullControl.All"))
	if readable and can_find_sites:
		message = _("Connected to Microsoft 365. Permissions granted to the app: {0}.").format(", ".join(roles))
	elif readable:
		message = _(
			"Connected, with {0}. Also add the application permission Sites.Read.All and grant admin consent; "
			"without it the app can't turn a pasted file link into the file."
		).format(", ".join(roles))
	else:
		message = _(
			"The credentials work, but the token has no file permission ({0}). In Entra ID open the app → API permissions, "
			"add Microsoft Graph → Application permissions → Files.Read.All and Sites.Read.All, then click "
			"'Grant admin consent'. It can take a few minutes to apply."
		).format(", ".join(roles) or _("none"))
	return {"ok": bool(readable and can_find_sites), "roles": roles, "message": message}


@frappe.whitelist()
def get_overview():
	"""Numbers for the settings page."""
	frappe.only_for(ROLE)
	last_poll = frappe.cache().get_value(LAST_POLL_KEY)
	scheduler_ok = bool(last_poll) and time_diff_in_seconds(now_datetime(), get_datetime(last_poll)) < 600
	return {
		"last_poll": last_poll,
		"scheduler_ok": scheduler_ok,
		"scheduler_disabled": is_scheduler_disabled(verbose=False),
		"watches": frappe.db.count(WATCH, {"enabled": 1}),
		"errors": frappe.db.count(WATCH, {"enabled": 1, "status": "Error"}),
		"pending": frappe.db.count(WATCH, {"enabled": 1, "status": "Changes Pending"}),
		"failed_logs": frappe.db.count(LOG, {"status": "Failed"}),
	}


@frappe.whitelist()
def resolve_file(watch: str):
	frappe.only_for(ROLE)
	doc = frappe.get_doc(WATCH, watch)
	doc.resolve_file()
	doc.save()
	return {"file_name": doc.file_name, "web_url": doc.file_web_url}


@frappe.whitelist()
def send_test_message(watch: str):
	frappe.only_for(ROLE)
	doc = frappe.get_doc(WATCH, watch)
	url = doc.get_password("webhook_url", raise_exception=False)
	if not url:
		frappe.throw(_("Paste the Teams workflow URL into the watch and save first."))
	card = build_test_card(
		file_name=doc.file_name or doc.watch_name,
		file_url=doc.file_web_url,
		language=get_settings().card_language or "English",
	)
	try:
		status = post_card(url, card)
	except TeamsPostError as exc:
		frappe.throw(str(exc), title=_("Teams did not accept the message"))
	return _("Sent ({0}). Check the channel in Teams.").format(status)


@frappe.whitelist()
def check_now(watch: str):
	frappe.only_for(ROLE)
	return run_watch(watch)


@frappe.whitelist()
def send_pending_now(watch: str):
	frappe.only_for(ROLE)
	return run_watch(watch, force_send=True)


@frappe.whitelist()
def reset_baseline(watch: str):
	frappe.only_for(ROLE)
	reset_state(watch)
	frappe.db.commit()
	return run_watch(watch)


@frappe.whitelist()
def get_status(watch: str):
	frappe.only_for(ROLE)
	name = frappe.db.get_value(STATE, {"watch": watch})
	if not name:
		return {}
	fields = [
		"baseline_taken_at",
		"last_checked_at",
		"session_started_at",
		"last_change_at",
		"session_editors",
		"error_count",
		"last_error",
		"last_error_at",
	]
	values = frappe.db.get_value(STATE, name, fields, as_dict=True) or {}
	settings = get_settings()
	if values.get("last_change_at"):
		quiet = cint(settings.quiet_minutes) or 5
		values["send_after"] = frappe.utils.add_to_date(values["last_change_at"], minutes=quiet)
	values["state"] = name
	return values


@frappe.whitelist()
def resend_log(log: str):
	frappe.only_for(ROLE)
	doc = frappe.get_doc(LOG, log)
	ok, error = deliver_log(doc.name, resend_all=doc.status == "Sent")
	if not ok:
		frappe.throw(error, title=_("Posting to Teams failed"))
	return _("Posted to Teams.")
