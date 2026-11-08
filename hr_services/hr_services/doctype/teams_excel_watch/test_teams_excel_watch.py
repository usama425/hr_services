# Copyright (c) 2026, ERC IT and contributors
# For license information, please see license.txt
"""End-to-end test of a watch, with Microsoft Graph and Teams replaced by fakes.

Run with:  bench --site <site> run-tests --app hr_services
"""

import base64
import datetime as dt
import io
import json
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime
from openpyxl import Workbook

from hr_services.teams_excel_notify import api, tasks
from hr_services.teams_excel_notify.graph import GraphClient, GraphError
from hr_services.teams_excel_notify.teams_card import TeamsPostError

AHMED = {"displayName": "Ahmed Ali", "email": "ahmed@contoso.com", "id": "u1"}
SARA = {"displayName": "Sara Khan", "email": "sara@contoso.com", "id": "u2"}
WATCH_NAME = "_Test HR Employees"


class FakeDrive:
	"""One Excel file in 'SharePoint' that the test edits."""

	def __init__(self):
		self.rows = [
			["Name", "Dept", "Salary", "Status"],
			["Ahmed", "IT", 5000, "Active"],
			["Bilal", "HR", 4000, "Active"],
			["Sara", "Finance", 4500, "Leave"],
		]
		self.version = 1
		self.editor = AHMED
		self.modified = dt.datetime(2026, 11, 7, 13, 40, 0)
		self.error = None

	def edit(self, editor, change):
		change(self.rows)
		self.version += 1
		self.editor = editor
		self.modified += dt.timedelta(minutes=1)

	def touch(self, editor):
		"""A save that changes nothing (formatting)."""
		self.version += 1
		self.editor = editor

	def content(self):
		wb = Workbook()
		ws = wb.active
		ws.title = "Employees"
		for row in self.rows:
			ws.append(row)
		out = io.BytesIO()
		wb.save(out)
		return out.getvalue()

	def item(self):
		return {
			"id": "ITEM1",
			"name": "Employees.xlsx",
			"size": 1000,
			"file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
			"parentReference": {"driveId": "b!DRIVE"},
			"webUrl": "https://contoso.sharepoint.com/sites/HR/Shared%20Documents/General/Employees.xlsx",
			"cTag": f'"c:{{0B5D}},{self.version}"',
			"lastModifiedDateTime": self.modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
			"lastModifiedBy": {"user": self.editor},
		}


class TestTeamsExcelWatch(FrappeTestCase):
	def setUp(self):
		self.drive = FakeDrive()
		self.posted = []
		self.post_error = None
		drive = self.drive

		def get_item(client, drive_id, item_id):
			if drive.error:
				raise drive.error
			return drive.item()

		def post(url, card, timeout=30):
			if self.post_error:
				raise self.post_error
			self.posted.append(card)
			return "HTTP 202"

		claims = json.dumps({"roles": ["Files.Read.All", "Sites.Read.All"]}).encode()
		token_payload = base64.urlsafe_b64encode(claims).decode().rstrip("=")
		self.patches = [
			mock.patch.object(GraphClient, "get_token", lambda client, force=False: f"h.{token_payload}.s"),
			mock.patch.object(GraphClient, "get_item", get_item),
			mock.patch.object(GraphClient, "download", lambda client, d, i, max_bytes: drive.content()),
			mock.patch.object(GraphClient, "resolve_link", lambda client, link: drive.item()),
			mock.patch.object(tasks, "post_card", post),
			mock.patch.object(api, "post_card", post),
		]
		for patch in self.patches:
			patch.start()

		settings = frappe.get_single("Teams Notify Settings")
		settings.update({
			"enabled": 1,
			"tenant_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
			"client_id": "11111111-2222-3333-4444-555555555555",
			"client_secret": "secret-value",
			"quiet_minutes": 5,
			"max_wait_minutes": 30,
			"card_language": "English",
		})
		settings.save()
		self.cleanup()
		self.watch = frappe.get_doc({
			"doctype": "Teams Excel Watch",
			"watch_name": WATCH_NAME,
			"department": "HR",
			"file_link": "https://contoso.sharepoint.com/:x:/s/HR/EUabc123?e=xyz",
			"webhook_url": "https://example.invalid/workflows/abc/triggers/manual/paths/invoke?sig=" + "x" * 250,
		}).insert()
		frappe.db.commit()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()
		self.cleanup()
		frappe.db.commit()

	def cleanup(self):
		for doctype in ("Teams Excel Change Log", "Teams Excel Watch State"):
			frappe.db.delete(doctype, {"watch": WATCH_NAME})
		frappe.db.delete("Teams Excel Watch", {"name": WATCH_NAME})
		frappe.db.delete("Error Log", {"method": ("like", "Teams Excel Notify%")})

	def watch_field(self, field):
		return frappe.db.get_value("Teams Excel Watch", WATCH_NAME, field)

	def state(self):
		return tasks.get_state(WATCH_NAME)

	def pass_quiet_period(self, minutes=6):
		name = frappe.db.get_value("Teams Excel Watch State", {"watch": WATCH_NAME})
		frappe.db.set_value("Teams Excel Watch State", name, "last_change_at", add_to_date(now_datetime(), minutes=-minutes))
		frappe.db.commit()

	def card_lines(self, card):
		lines = []
		for block in card["body"]:
			if block["type"] == "TextBlock":
				lines.append(block["text"])
			elif block["type"] == "RichTextBlock":
				lines.append("".join(r["text"] for r in block["inlines"]))
			elif block["type"] == "FactSet":
				lines += [f"{f['title']}: {f['value']}" for f in block["facts"]]
		return lines

	# ------------------------------------------------------------------ tests

	def test_link_is_resolved_on_save(self):
		self.assertEqual(self.watch.drive_id, "b!DRIVE")
		self.assertEqual(self.watch.item_id, "ITEM1")
		self.assertEqual(self.watch.file_name, "Employees.xlsx")
		self.assertEqual(self.watch.status, "New")
		self.assertTrue(self.watch.get_password("webhook_url").startswith("https://example.invalid/"))

	def test_full_flow_two_editors(self):
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "baseline")
		self.assertEqual(self.watch_field("status"), "Watching")
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "unchanged")

		self.drive.edit(AHMED, lambda rows: rows[2].__setitem__(2, 6000))
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "pending")
		self.assertEqual(self.watch_field("status"), "Changes Pending")
		self.drive.edit(SARA, lambda rows: rows.append(["Zaid", "Sales", 3500, "Active"]))
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "pending")
		self.assertEqual(self.watch_field("pending_editors"), "Ahmed Ali, Sara Khan")

		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "unchanged")  # quiet period not over
		self.assertEqual(self.posted, [])

		self.pass_quiet_period()
		result = tasks.run_watch(WATCH_NAME)
		self.assertEqual(result["outcome"], "sent")
		self.assertEqual(len(self.posted), 1)
		lines = self.card_lines(self.posted[0])
		self.assertEqual(lines[0], "Employees.xlsx was updated")
		self.assertIn("Edited by: Ahmed Ali, Sara Khan", lines)
		self.assertIn("Row 3 · Bilal — Salary: 4000 → 6000", lines)
		self.assertIn("Row 5 added · Zaid — Name: Zaid, Dept: Sales, Salary: 3500, Status: Active", lines)

		log = frappe.get_doc("Teams Excel Change Log", result["log"])
		self.assertEqual((log.status, log.cards_sent, log.change_count), ("Sent", 1, 2))
		self.assertIn("(by Sara Khan)", log.summary)
		self.assertEqual(self.watch_field("status"), "Watching")
		self.assertTrue(self.watch_field("last_notified_at"))

		# The new baseline includes those edits: nothing more to send.
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "unchanged")

	def test_undo_and_formatting_send_nothing(self):
		tasks.run_watch(WATCH_NAME)
		self.drive.touch(AHMED)
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "formatting")
		self.drive.edit(AHMED, lambda rows: rows[1].__setitem__(3, "Left"))
		tasks.run_watch(WATCH_NAME)
		self.drive.edit(AHMED, lambda rows: rows[1].__setitem__(3, "Active"))
		tasks.run_watch(WATCH_NAME)
		self.pass_quiet_period()
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "reverted")
		self.assertEqual(self.posted, [])

	def test_send_now_and_long_edit_limit(self):
		tasks.run_watch(WATCH_NAME)
		self.drive.edit(AHMED, lambda rows: rows[3].__setitem__(3, "Approved"))
		tasks.run_watch(WATCH_NAME)
		self.assertEqual(api.send_pending_now(WATCH_NAME)["outcome"], "sent")
		self.assertEqual(len(self.posted), 1)

		self.drive.edit(SARA, lambda rows: rows[3].__setitem__(2, 4600))
		tasks.run_watch(WATCH_NAME)
		name = frappe.db.get_value("Teams Excel Watch State", {"watch": WATCH_NAME})
		frappe.db.set_value("Teams Excel Watch State", name, "session_started_at", add_to_date(now_datetime(), minutes=-31))
		frappe.db.commit()
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "sent")  # 30-minute ceiling

	def test_errors_are_recorded_once_and_cleared(self):
		tasks.run_watch(WATCH_NAME)
		self.drive.error = GraphError("The file was not found.", 404, "itemNotFound")
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "error")
		tasks.run_watch(WATCH_NAME)
		self.assertEqual(self.watch_field("status"), "Error")
		self.assertEqual(self.watch_field("last_error"), "The file was not found.")
		self.assertEqual(frappe.db.count("Error Log", {"method": ("like", "Teams Excel Notify%")}), 1)
		self.assertEqual(self.state().error_count, 2)

		self.drive.error = None
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "unchanged")
		self.assertEqual(self.watch_field("status"), "Watching")
		self.assertFalse(self.watch_field("last_error"))

	def test_failed_post_is_retried(self):
		tasks.run_watch(WATCH_NAME)
		self.drive.edit(AHMED, lambda rows: rows[2].__setitem__(3, "Left"))
		tasks.run_watch(WATCH_NAME)
		self.pass_quiet_period()
		self.post_error = TeamsPostError("Teams workflow answered HTTP 400")
		result = tasks.run_watch(WATCH_NAME)
		self.assertEqual(result["outcome"], "send_failed")
		log = frappe.get_doc("Teams Excel Change Log", result["log"])
		self.assertEqual((log.status, log.attempts), ("Failed", 1))

		self.post_error = None
		tasks.retry_pending_logs()  # too early: waits 2 minutes
		self.assertEqual(frappe.db.get_value("Teams Excel Change Log", log.name, "status"), "Failed")
		frappe.db.sql("update `tabTeams Excel Change Log` set modified = %s where name = %s", (add_to_date(now_datetime(), minutes=-3), log.name))
		tasks.retry_pending_logs()
		self.assertEqual(frappe.db.get_value("Teams Excel Change Log", log.name, "status"), "Sent")
		self.assertEqual(len(self.posted), 1)

	def test_settings_change_resets_starting_point(self):
		tasks.run_watch(WATCH_NAME)
		self.assertTrue(frappe.db.exists("Teams Excel Watch State", {"watch": WATCH_NAME}))
		watch = frappe.get_doc("Teams Excel Watch", WATCH_NAME)
		watch.ignore_columns = "Status"
		watch.save()
		self.assertFalse(frappe.db.exists("Teams Excel Watch State", {"watch": WATCH_NAME}))
		self.assertEqual(self.watch_field("status"), "New")
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "baseline")

	def test_form_save_keeps_status_fields(self):
		tasks.run_watch(WATCH_NAME)
		stale = frappe.get_doc("Teams Excel Watch", WATCH_NAME)
		stale.status = "New"
		stale.channel_label = "HR › General"
		stale.save()
		self.assertEqual(self.watch_field("status"), "Watching")
		self.assertEqual(self.watch_field("channel_label"), "HR › General")

	def test_scheduler_entry_and_buttons(self):
		tasks.poll_all()
		self.assertEqual(self.watch_field("status"), "Watching")
		self.assertTrue(frappe.cache().get_value(tasks.LAST_POLL_KEY))
		connection = api.test_connection()
		self.assertTrue(connection["ok"])
		self.assertEqual(connection["roles"], ["Files.Read.All", "Sites.Read.All"])
		self.assertIn("Sent", api.send_test_message(WATCH_NAME))
		self.assertEqual(len(self.posted), 1)
		overview = api.get_overview()
		self.assertTrue(overview["scheduler_ok"])
		self.assertGreaterEqual(overview["watches"], 1)
		self.assertEqual(api.reset_baseline(WATCH_NAME)["outcome"], "baseline")
		self.assertTrue(api.get_status(WATCH_NAME)["baseline_taken_at"])

	def test_disabled_watch_is_skipped_and_delete_cleans_up(self):
		tasks.run_watch(WATCH_NAME)
		watch = frappe.get_doc("Teams Excel Watch", WATCH_NAME)
		watch.enabled = 0
		watch.save()
		self.assertEqual(self.watch_field("status"), "Disabled")
		self.drive.edit(AHMED, lambda rows: rows[1].__setitem__(3, "Left"))
		tasks.poll_all()
		self.assertEqual(self.watch_field("status"), "Disabled")
		frappe.delete_doc("Teams Excel Watch", WATCH_NAME)
		self.assertFalse(frappe.db.exists("Teams Excel Watch State", {"watch": WATCH_NAME}))

	def test_manual_ids_skip_link_lookup(self):
		with mock.patch.object(GraphClient, "resolve_link", side_effect=GraphError("Could not find the file from this link.")):
			watch = frappe.get_doc("Teams Excel Watch", WATCH_NAME)
			watch.file_link = "https://contoso.sharepoint.com/:x:/s/HR/EUother"
			self.assertRaises(frappe.ValidationError, watch.save)
			watch.reload()
			watch.file_link = "https://contoso.sharepoint.com/:x:/s/HR/EUother"
			watch.drive_id, watch.item_id = "b!MANUAL", "ITEM9"
			watch.save()
		self.assertEqual(self.watch_field("item_id"), "ITEM9")

	def test_reenable_starts_fresh(self):
		tasks.run_watch(WATCH_NAME)
		watch = frappe.get_doc("Teams Excel Watch", WATCH_NAME)
		watch.enabled = 0
		watch.save()
		self.drive.edit(AHMED, lambda rows: rows[1].__setitem__(3, "Left"))
		watch.reload()
		watch.enabled = 1
		watch.save()
		self.assertEqual(self.watch_field("status"), "New")
		self.assertEqual(tasks.run_watch(WATCH_NAME)["outcome"], "baseline")
		self.assertEqual(self.posted, [])

	def test_cards_json_is_stored(self):
		tasks.run_watch(WATCH_NAME)
		self.drive.edit(AHMED, lambda rows: rows[2].__setitem__(1, "Payroll"))
		tasks.run_watch(WATCH_NAME)
		result = api.send_pending_now(WATCH_NAME)
		log = frappe.get_doc("Teams Excel Change Log", result["log"])
		self.assertEqual(json.loads(log.cards_json)[0]["type"], "AdaptiveCard")
		self.assertEqual(json.loads(log.changes_json)["total"], 1)
		self.assertEqual(api.resend_log(log.name), "Posted to Teams.")
		self.assertEqual(len(self.posted), 2)
