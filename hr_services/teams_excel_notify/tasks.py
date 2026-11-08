"""The scheduled check: look at each watched file, compare, and notify Teams.

Runs every minute (see hooks.py). For each enabled Teams Excel Watch it:
1. reads the file's metadata from Microsoft Graph (one small request);
2. if the content tag changed, downloads the file and records the step;
3. once nobody has edited for the quiet period, posts one summary card.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, convert_utc_to_system_timezone, get_datetime, now_datetime, time_diff_in_seconds

from hr_services.teams_excel_notify.differ import DiffConfig, Session, report_text
from hr_services.teams_excel_notify.excel_reader import DEFAULT_MAX_CELLS, WorkbookError, read_workbook
from hr_services.teams_excel_notify.graph import GraphClient, GraphError, check_excel_item, editor_from_item, parse_graph_time
from hr_services.teams_excel_notify.teams_card import TeamsPostError, build_cards, post_card
from hr_services.teams_excel_notify.utils import pack, split_list, unpack

WATCH = "Teams Excel Watch"
STATE = "Teams Excel Watch State"
LOG = "Teams Excel Change Log"
SETTINGS = "Teams Notify Settings"

LOCK_TTL = 15 * 60  # seconds; a crashed run frees its lock after this
MAX_SEND_ATTEMPTS = 5
SCHEDULER_SLACK = 20  # seconds of tolerance for the once-a-minute scheduler
LAST_POLL_KEY = "teams_excel_notify:last_poll"


# --------------------------------------------------------------------------- plumbing


def get_settings():
	return frappe.get_single(SETTINGS)


def graph_client(settings=None) -> GraphClient:
	settings = settings or get_settings()
	secret = settings.get_password("client_secret", raise_exception=False) or ""
	return GraphClient(settings.tenant_id, settings.client_id, secret)


def _lock_key(name: str):
	return frappe.cache().make_key(f"teams_excel_notify:lock:{name}")


def acquire_lock(name: str, ttl: int = LOCK_TTL) -> bool:
	return bool(frappe.cache().set(_lock_key(name), 1, nx=True, ex=ttl))


def release_lock(name: str) -> None:
	frappe.cache().delete(_lock_key(name))


def get_state(watch_name: str):
	name = frappe.db.get_value(STATE, {"watch": watch_name})
	if name:
		return frappe.get_doc(STATE, name)
	state = frappe.new_doc(STATE)
	state.watch = watch_name
	return state


def save_state(state) -> None:
	if state.is_new():
		state.insert(ignore_permissions=True)
	else:
		state.save(ignore_permissions=True)


def reset_state(watch_name: str, status: str = "New") -> None:
	"""Forget the baseline and any pending changes; the next check starts fresh."""
	for name in frappe.get_all(STATE, filters={"watch": watch_name}, pluck="name"):
		frappe.delete_doc(STATE, name, ignore_permissions=True, force=True)
	if frappe.db.exists(WATCH, watch_name):
		set_watch_display(watch_name, status=status, pending_editors=None, last_error=None)


def set_watch_display(name: str, **values) -> None:
	"""Update the read-only status fields on the watch without touching 'modified'."""
	frappe.db.set_value(WATCH, name, values, update_modified=False)


def diff_config(watch) -> DiffConfig:
	return DiffConfig.build(watch.header_row, watch.label_columns or "A", watch.ignore_columns)


def read_snapshot(client: GraphClient, watch, settings, item: dict) -> dict:
	check_excel_item(item)
	max_bytes = max(1, cint(settings.max_file_mb) or 25) * 1024 * 1024
	if cint(item.get("size")) > max_bytes:
		raise GraphError(
			_("{0} is larger than {1} MB, the limit set in Teams Notify Settings.").format(
				item.get("name"), cint(settings.max_file_mb) or 25
			)
		)
	content = client.download(watch.drive_id, watch.item_id, max_bytes)
	return read_workbook(
		content,
		only_sheets=split_list(watch.sheets) or None,
		ignore_formulas=bool(cint(watch.ignore_formula_cells)),
		max_cells=cint(settings.max_cells) or DEFAULT_MAX_CELLS,
	)


def _set_baseline(state, snapshot: dict, ctag: str, now) -> None:
	state.baseline_data = pack(snapshot)
	state.baseline_ctag = ctag
	state.baseline_taken_at = now
	state.session_data = None
	state.session_started_at = None
	state.last_change_at = None
	state.session_editors = None


def _is_due(state, settings, now) -> bool:
	quiet = max(1, cint(settings.quiet_minutes) or 5) * 60
	longest = max(quiet, (cint(settings.max_wait_minutes) or 30) * 60)
	if state.last_change_at and time_diff_in_seconds(now, state.last_change_at) >= quiet - SCHEDULER_SLACK:
		return True
	if state.session_started_at and time_diff_in_seconds(now, state.session_started_at) >= longest - SCHEDULER_SLACK:
		return True
	return False


def _local(utc_text, fallback):
	parsed = parse_graph_time(utc_text)
	if not parsed:
		return get_datetime(fallback) if fallback else None
	return convert_utc_to_system_timezone(parsed).replace(tzinfo=None)


def format_when(meta: dict, start_fallback, end_fallback, language: str) -> str:
	start = _local(meta.get("first_modified"), start_fallback) or end_fallback
	end = _local(meta.get("last_modified"), end_fallback) or start
	if start > end:
		start, end = end, start
	arabic = str(language or "").lower().startswith("ar")
	day_fmt = "%Y-%m-%d" if arabic else "%a %d %b %Y"
	if start.date() == end.date():
		text = f"{start.strftime(day_fmt)}, {start.strftime('%H:%M')}"
		if end.strftime("%H:%M") != start.strftime("%H:%M"):
			text += f" – {end.strftime('%H:%M')}"
		return text
	short = "%Y-%m-%d %H:%M" if arabic else "%d %b %H:%M"
	return f"{start.strftime(short)} – {end.strftime(short)}"


# --------------------------------------------------------------------------- one watch


def process_watch(name: str, settings, force_send: bool = False, client: GraphClient | None = None) -> dict:
	"""Check one file once. Raises on errors (the caller records them)."""
	watch = frappe.get_doc(WATCH, name)
	if not (watch.drive_id and watch.item_id):
		raise GraphError(_("The file is not connected yet. Paste its link in the watch and save."))
	client = client or graph_client(settings)
	cfg = diff_config(watch)
	state = get_state(name)
	now = now_datetime()

	item = client.get_item(watch.drive_id, watch.item_id)
	ctag = item.get("cTag") or item.get("eTag") or item.get("lastModifiedDateTime") or ""
	if item.get("name") and (item.get("name") != watch.file_name or item.get("webUrl") != watch.file_web_url):
		frappe.db.set_value(WATCH, name, {"file_name": item.get("name"), "file_web_url": item.get("webUrl")}, update_modified=False)
		watch.file_name, watch.file_web_url = item.get("name"), item.get("webUrl")

	result = {"outcome": "unchanged", "message": _("No changes since the last check."), "log": None}

	if not state.baseline_data:
		snapshot = read_snapshot(client, watch, settings, item)
		_set_baseline(state, snapshot, ctag, now)
		state.last_ctag = ctag
		result.update(
			outcome="baseline",
			message=_("Connected to {0}. Its current content is the starting point; changes from now on go to Teams.").format(
				item.get("name")
			),
		)
	elif ctag != state.last_ctag:
		snapshot = read_snapshot(client, watch, settings, item)
		baseline = unpack(state.baseline_data)
		if state.session_data:
			session = Session.from_dict(unpack(state.session_data), cfg)
		else:
			session = Session.start(baseline, cfg)
		editor = editor_from_item(item)
		changed = session.step(snapshot, editor)
		state.last_ctag = ctag
		if session.has_changes:
			if changed:
				session.meta.setdefault("first_modified", item.get("lastModifiedDateTime"))
				session.meta["last_modified"] = item.get("lastModifiedDateTime")
				names = session.meta.setdefault("editor_names", [])
				if editor["name"] not in names:
					names.append(editor["name"])
			state.session_started_at = state.session_started_at or now
			state.last_change_at = now
			state.session_data = pack(session.to_dict())
			state.session_editors = ", ".join(session.meta.get("editor_names") or [editor["name"]])
			result.update(
				outcome="pending",
				message=_("Changes by {0} found. They will be posted after {1} minute(s) without edits.").format(
					state.session_editors, cint(settings.quiet_minutes) or 5
				),
			)
		else:
			_set_baseline(state, snapshot, ctag, now)
			result.update(outcome="formatting", message=_("The file was saved, but no cell values changed."))

	if state.session_data and (force_send or _is_due(state, settings, now)):
		session = Session.from_dict(unpack(state.session_data), cfg)
		baseline = unpack(state.baseline_data)
		report = session.finalize(baseline, include_hidden=bool(cint(watch.include_hidden_sheets)))
		if report["items"]:
			log = _create_log(watch, state, session, report, settings, now)
			result.update(
				outcome="sent",
				message=_("{0} change(s) found and sent to Teams.").format(report["total"]),
				log=log.name,
			)
		else:
			result.update(outcome="reverted", message=_("The edits were undone before the quiet period ended, so nothing was sent."))
		_set_baseline(state, session.cur, state.last_ctag, now)

	state.last_checked_at = now
	state.error_count = 0
	state.last_error = None
	save_state(state)
	pending = bool(state.session_data)
	set_watch_display(
		name,
		status="Changes Pending" if pending else "Watching",
		last_checked_at=now,
		last_error=None,
		pending_editors=state.session_editors if pending else None,
	)
	return result


def _create_log(watch, state, session: Session, report: dict, settings, now):
	when = format_when(session.meta, state.session_started_at, now, settings.card_language)
	cards = build_cards(
		report,
		file_name=watch.file_name or _("Excel file"),
		file_url=watch.file_web_url,
		when_text=when,
		language=settings.card_language or "English",
		max_items=cint(settings.max_changes_per_card) or 25,
		max_cards=cint(settings.max_cards_per_update) or 3,
	)
	editors = [report["editors"][i]["name"] for i in report["active_editors"] if i < len(report["editors"])]
	return frappe.get_doc(
		{
			"doctype": LOG,
			"watch": watch.name,
			"department": watch.department,
			"file_name": watch.file_name,
			"status": "Pending",
			"editors": ", ".join(editors),
			"session_start": state.session_started_at or now,
			"session_end": now,
			"change_count": report["total"],
			"summary": report_text(report),
			"changes_json": json.dumps(report, ensure_ascii=False, indent=1),
			"cards_json": json.dumps(cards, ensure_ascii=False),
			"cards_total": len(cards),
		}
	).insert(ignore_permissions=True)


def friendly_error(exc: Exception) -> str:
	if isinstance(exc, (GraphError, WorkbookError, TeamsPostError)):
		return str(exc)
	return _("Unexpected error: {0}. Details are in the Error Log.").format(str(exc)[:300] or exc.__class__.__name__)


def record_error(name: str, exc: Exception) -> str:
	"""Store the error on the watch; write an Error Log only when it is new (or hourly)."""
	message = friendly_error(exc)
	now = now_datetime()
	state = get_state(name)
	repeated = state.last_error == message
	state.error_count = cint(state.error_count) + 1
	state.last_error = message
	state.last_error_at = now
	state.last_checked_at = now
	save_state(state)
	if frappe.db.exists(WATCH, name):
		set_watch_display(name, status="Error", last_error=message, last_checked_at=now)
	if not repeated or state.error_count % 60 == 0:
		frappe.log_error(title=f"Teams Excel Notify: {name}"[:140], message=frappe.get_traceback() or message)
	return message


def run_watch(name: str, force_send: bool = False, settings=None, client: GraphClient | None = None) -> dict:
	"""Check one watch with locking, error recording and delivery. Never raises."""
	settings = settings or get_settings()
	lock = f"watch:{name}"
	if not acquire_lock(lock):
		return {"outcome": "busy", "message": _("This file is being checked right now. Try again in a minute."), "log": None}
	try:
		try:
			result = process_watch(name, settings, force_send=force_send, client=client)
			frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			message = record_error(name, exc)
			frappe.db.commit()
			return {"outcome": "error", "message": message, "log": None}
		if result.get("log"):
			ok, error = deliver_log(result["log"])
			if not ok:
				result["message"] += " " + _("Posting to Teams failed: {0}").format(error)
				result["outcome"] = "send_failed"
		return result
	finally:
		release_lock(lock)


# --------------------------------------------------------------------------- delivery


def deliver_log(log_name: str, resend_all: bool = False):
	"""Post the log's cards that have not been posted yet. Returns (ok, error)."""
	log = frappe.get_doc(LOG, log_name)
	cards = json.loads(log.cards_json or "[]")
	sent = 0 if resend_all else cint(log.cards_sent)
	error = None
	url = None
	if frappe.db.exists(WATCH, log.watch):
		url = frappe.get_doc(WATCH, log.watch).get_password("webhook_url", raise_exception=False)
	if not url:
		error = _("The watch has no Teams workflow URL.")
	else:
		for card in cards[sent:]:
			try:
				post_card(url, card)
			except TeamsPostError as exc:
				error = str(exc)
				break
			sent += 1
	now = now_datetime()
	values = {"cards_sent": sent, "attempts": cint(log.attempts) + 1}
	if error:
		values.update(status="Failed", response=error[:1000])
	else:
		values.update(status="Sent", sent_at=now, response=_("Posted {0} card(s) to Teams.").format(len(cards)))
	log.db_set(values)
	if not error and frappe.db.exists(WATCH, log.watch):
		set_watch_display(log.watch, last_notified_at=now)
	frappe.db.commit()
	return (error is None), error


def retry_pending_logs() -> None:
	"""Re-send notifications that failed (2, 4, 6, 8 minutes apart, 5 tries in total)."""
	now = now_datetime()
	logs = frappe.get_all(
		LOG,
		filters={"status": ("in", ("Pending", "Failed")), "attempts": ("<", MAX_SEND_ATTEMPTS)},
		fields=["name", "attempts", "modified"],
		order_by="creation asc",
		limit=20,
	)
	for log in logs:
		if time_diff_in_seconds(now, log.modified) >= 120 * max(1, cint(log.attempts)):
			deliver_log(log.name)


# --------------------------------------------------------------------------- scheduler


def poll_all() -> None:
	"""Scheduler entry point, every minute."""
	frappe.cache().set_value(LAST_POLL_KEY, str(now_datetime()), expires_in_sec=7 * 24 * 3600)
	settings = get_settings()
	if not cint(settings.enabled) or not settings.is_configured():
		return
	if not acquire_lock("poll"):
		return
	try:
		client = graph_client(settings)
		for name in frappe.get_all(WATCH, filters={"enabled": 1}, pluck="name", order_by="creation asc"):
			run_watch(name, settings=settings, client=client)
		retry_pending_logs()
	finally:
		release_lock("poll")
