"""Tests for the Teams Adaptive Card builder (no Frappe needed)."""

import json
import unittest
from unittest import mock

from hr_services.teams_excel_notify.differ import build_report
from hr_services.teams_excel_notify.teams_card import MAX_CARD_BYTES, TeamsPostError, build_cards, build_test_card, post_card

EDITORS = [{"name": "Ahmed Ali", "email": "a@x.com", "id": "1"}, {"name": "Sara Khan", "email": "s@x.com", "id": "2"}]


def cell(row, label, col, old, new, by=0, sheet="Employees"):
	return {"sheet": sheet, "kind": "cell", "row": row, "old_row": row, "moved": False, "label": label,
			"cells": [{"col": col, "old": old, "new": new}], "by": by}


def texts(card):
	out = []
	for block in card["body"]:
		if block["type"] == "TextBlock":
			out.append(block["text"])
		elif block["type"] == "RichTextBlock":
			out.append("".join(run["text"] for run in block["inlines"]))
		elif block["type"] == "FactSet":
			out += [f"{f['title']}: {f['value']}" for f in block["facts"]]
	return out


class TestCards(unittest.TestCase):
	def test_single_editor_card(self):
		report = build_report([
			cell(5, "Bilal", "Salary", "5000", "6000"),
			{"sheet": "Employees", "kind": "row_added", "row": 15, "label": "Sara", "values": [["Name", "Sara"], ["Salary", "4500"]], "by": 0},
		], EDITORS)
		cards = build_cards(report, file_name="Tracker.xlsx", file_url="https://contoso.sharepoint.com/x", when_text="Sat 07 Nov 2026, 16:42")
		self.assertEqual(len(cards), 1)
		lines = texts(cards[0])
		self.assertEqual(lines[0], "Tracker.xlsx was updated")
		self.assertIn("Edited by: Ahmed Ali", lines)
		self.assertIn("Sheet: Employees", lines)
		self.assertIn("Changes: 1 cell changed · 1 row added", lines)
		self.assertIn("Row 5 · Bilal — Salary: 5000 → 6000", lines)
		self.assertIn("Row 15 added · Sara — Name: Sara, Salary: 4500", lines)
		self.assertEqual(cards[0]["actions"][0]["url"], "https://contoso.sharepoint.com/x")
		self.assertEqual(cards[0]["version"], "1.4")

	def test_several_editors_are_grouped(self):
		report = build_report([cell(5, "Bilal", "Salary", "5000", "6000", by=0), cell(9, "Omar", "Status", "Active", "Left", by=1)], EDITORS)
		lines = texts(build_cards(report, file_name="T.xlsx", file_url=None, when_text="now")[0])
		self.assertIn("Edited by: Ahmed Ali, Sara Khan", lines)
		self.assertLess(lines.index("Ahmed Ali"), lines.index("Row 5 · Bilal — Salary: 5000 → 6000"))
		self.assertLess(lines.index("Sara Khan"), lines.index("Row 9 · Omar — Status: Active → Left"))

	def test_long_lists_split_and_overflow(self):
		items = [cell(i + 2, f"Person {i}", "Salary", str(i), str(i + 1)) for i in range(100)]
		cards = build_cards(build_report(items, EDITORS), file_name="T.xlsx", file_url=None, when_text="now", max_items=25, max_cards=3)
		self.assertEqual(len(cards), 3)
		self.assertIn("(1/3)", texts(cards[0])[0])
		self.assertIn("…and 25 more changes. Open the file to see everything.", texts(cards[-1]))
		for card in cards:
			self.assertLess(len(json.dumps(card).encode()), MAX_CARD_BYTES)

	def test_huge_values_stay_under_size_limit(self):
		items = [cell(i + 2, "x" * 500, "Notes", "a" * 3000, "b" * 3000) for i in range(25)]
		cards = build_cards(build_report(items, EDITORS), file_name="T.xlsx", file_url=None, when_text="now", max_cards=10)
		for card in cards:
			self.assertLess(len(json.dumps(card, ensure_ascii=False).encode()), MAX_CARD_BYTES)

	def test_empty_values_and_arabic(self):
		report = build_report([cell(3, "", "Notes", "", "done")], EDITORS)
		card = build_cards(report, file_name="T.xlsx", file_url=None, when_text="now", language="Arabic")[0]
		lines = texts(card)
		self.assertEqual(lines[0], "تم تحديث T.xlsx")
		self.assertIn("الصف 3 — Notes: (فارغ) ← done", lines)
		self.assertEqual(card["body"][0]["horizontalAlignment"], "Right")

	def test_test_card(self):
		card = build_test_card(file_name="T.xlsx", file_url="https://x")
		self.assertIn("T.xlsx", card["body"][0]["text"])

	def test_post_card(self):
		ok = mock.Mock(status_code=202, text="")
		with mock.patch("hr_services.teams_excel_notify.teams_card.requests.post", return_value=ok) as post:
			self.assertEqual(post_card("https://example.invalid/hook", {"type": "AdaptiveCard"}), "HTTP 202")
			payload = post.call_args.kwargs["json"]
			self.assertEqual(payload["attachments"][0]["contentType"], "application/vnd.microsoft.card.adaptive")
		bad = mock.Mock(status_code=400, text="The request content was invalid")
		with mock.patch("hr_services.teams_excel_notify.teams_card.requests.post", return_value=bad):
			with self.assertRaises(TeamsPostError):
				post_card("https://example.invalid/hook", {})


if __name__ == "__main__":
	unittest.main()
