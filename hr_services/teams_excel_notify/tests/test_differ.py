"""Unit tests for the comparison engine (no Frappe needed).

Run with:  python -m pytest hr_services/teams_excel_notify/tests/test_differ.py
or as part of:  bench --site <site> run-tests --app hr_services
"""

import json
import unittest

from hr_services.teams_excel_notify.differ import (
	DiffConfig,
	Session,
	align_rows,
	column_index,
	column_letter,
	lis_positions,
	map_columns,
	report_text,
)

AHMED = {"name": "Ahmed Ali", "email": "ahmed@example.com", "id": "u1"}
SARA = {"name": "Sara Khan", "email": "sara@example.com", "id": "u2"}
HEADER = ["Name", "Dept", "Salary", "Status"]
BASE_ROWS = [
	HEADER,
	["Ahmed", "IT", "5000", "Active"],
	["Bilal", "HR", "4000", "Active"],
	["Sara", "Finance", "4500", "Leave"],
	["Omar", "IT", "3000", "Active"],
]


def snap(**sheets):
	return {
		"v": 1,
		"order": list(sheets),
		"sheets": {name: {"rows": [list(r) for r in rows], "hidden": False} for name, rows in sheets.items()},
	}


def rows_with(changes=None, extra=None, drop=None):
	rows = [list(r) for r in BASE_ROWS]
	for (r, c), value in (changes or {}).items():
		rows[r][c] = value
	if drop:
		rows = [row for i, row in enumerate(rows) if i not in drop]
	return rows + [list(e) for e in (extra or [])]


def run(steps, cfg=None, base=None, persist=True):
	"""steps: list of (snapshot, editor). Returns the final report."""
	cfg = cfg or DiffConfig.build(1, "A", "")
	base = base or snap(Employees=BASE_ROWS)
	session = Session.start(base, cfg)
	for snapshot, editor in steps:
		session.step(snapshot, editor)
		if persist:  # what the scheduler does between minutes
			session = Session.from_dict(json.loads(json.dumps(session.to_dict())), cfg)
	return session.finalize(base)


def kinds(report):
	return [item["kind"] for item in report["items"]]


def by_name(report, item):
	return report["editors"][item["by"]]["name"]


class TestHelpers(unittest.TestCase):
	def test_column_letters(self):
		self.assertEqual([column_letter(i) for i in (0, 25, 26, 51, 701, 702)], ["A", "Z", "AA", "AZ", "ZZ", "AAA"])
		for i in (0, 5, 27, 702, 16383):
			self.assertEqual(column_index(column_letter(i)), i)
		with self.assertRaises(ValueError):
			column_index("1")

	def test_lis(self):
		self.assertEqual(len(lis_positions([4, 2, 3, 1])), 2)
		self.assertEqual(lis_positions([0, 1, 2]), {0, 1, 2})
		self.assertEqual(lis_positions([]), set())

	def test_config(self):
		cfg = DiffConfig.build(2, "b, C", "Last updated\nModified  By")
		self.assertEqual(cfg.header_index, 1)
		self.assertEqual(cfg.label_columns, (1, 2))
		self.assertTrue(cfg.ignored("last UPDATED"))
		self.assertTrue(cfg.ignored("modified by"))
		self.assertIsNone(DiffConfig.build(0, "A", "").header_index)

	def test_map_columns_insert_and_rename(self):
		self.assertEqual(map_columns(["A", "B", "C", ""], ["A", "X", "B", "C"]), [0, None, 1, 2])
		self.assertEqual(map_columns(["Name", "Salary"], ["Name", "Basic Salary"]), [0, 1])
		# a header cleared in place is not a rename
		self.assertEqual(map_columns(["Name", "Notes"], ["Name", ""]), [0, None])

	def test_align_rows_insert_delete(self):
		prev = [("a",), ("b",), ("c",)]
		self.assertEqual(align_rows(prev, [("a",), ("x",), ("b",), ("c",)]), [0, None, 1, 2])
		self.assertEqual(align_rows(prev, [("a",), ("c",)]), [0, 2])


class TestSingleEditor(unittest.TestCase):
	def test_no_change(self):
		report = run([(snap(Employees=BASE_ROWS), AHMED)])
		self.assertEqual(report["items"], [])

	def test_cell_edit(self):
		report = run([(snap(Employees=rows_with({(2, 2): "6000"})), AHMED)])
		self.assertEqual(kinds(report), ["cell"])
		item = report["items"][0]
		self.assertEqual((item["row"], item["label"]), (3, "Bilal"))
		self.assertEqual(item["cells"], [{"col": "Salary", "old": "4000", "new": "6000"}])
		self.assertEqual(by_name(report, item), "Ahmed Ali")

	def test_two_cells_one_row_one_line(self):
		report = run([(snap(Employees=rows_with({(2, 2): "6000", (2, 3): "Promoted"})), AHMED)])
		self.assertEqual(kinds(report), ["cell"])
		self.assertEqual(len(report["items"][0]["cells"]), 2)
		self.assertEqual(report["total"], 2)

	def test_row_appended(self):
		report = run([(snap(Employees=rows_with(extra=[["Zaid", "Sales", "3500", "Active"]])), AHMED)])
		self.assertEqual(kinds(report), ["row_added"])
		self.assertEqual(report["items"][0]["row"], 6)
		self.assertEqual(report["items"][0]["values"][0], ["Name", "Zaid"])

	def test_row_inserted_in_middle_keeps_others_unchanged(self):
		rows = rows_with()
		rows.insert(2, ["Nadia", "HR", "4100", "Active"])
		report = run([(snap(Employees=rows), AHMED)])
		self.assertEqual(kinds(report), ["row_added"])
		self.assertEqual(report["items"][0]["row"], 3)

	def test_row_deleted(self):
		report = run([(snap(Employees=rows_with(drop={2})), AHMED)])
		self.assertEqual(kinds(report), ["row_removed"])
		self.assertEqual((report["items"][0]["row"], report["items"][0]["label"]), (3, "Bilal"))

	def test_edit_below_inserted_row_reports_new_position(self):
		rows = rows_with({(4, 2): "3300"})
		rows.insert(1, ["Nadia", "HR", "4100", "Active"])
		report = run([(snap(Employees=rows), AHMED)])
		cell = [i for i in report["items"] if i["kind"] == "cell"][0]
		self.assertEqual((cell["row"], cell["old_row"], cell["label"]), (6, 5, "Omar"))

	def test_sort_is_summarised_not_listed_as_edits(self):
		big = [HEADER] + [[f"P{i:02d}", "IT", str(1000 + i), "Active"] for i in range(30)]
		shuffled = [HEADER] + sorted(big[1:], key=lambda r: r[0], reverse=True)
		report = run([(snap(Employees=shuffled), AHMED)], base=snap(Employees=big))
		self.assertEqual(kinds(report), ["rows_moved"])
		self.assertEqual(report["items"][0]["count"], 29)

	def test_sort_plus_edit(self):
		big = [HEADER] + [[f"P{i:02d}", "IT", str(1000 + i), "Active"] for i in range(20)]
		new = [HEADER] + sorted([list(r) for r in big[1:]], key=lambda r: r[0], reverse=True)
		new[5][3] = "Resigned"
		report = run([(snap(Employees=new), AHMED)], base=snap(Employees=big))
		self.assertEqual(kinds(report), ["rows_moved", "cell"])
		cell = report["items"][1]
		self.assertEqual(cell["cells"], [{"col": "Status", "old": "Active", "new": "Resigned"}])
		self.assertEqual(cell["row"], 6)

	def test_single_row_moved(self):
		rows = rows_with()
		moved = rows.pop(4)
		rows.insert(1, moved)
		report = run([(snap(Employees=rows), AHMED)])
		self.assertEqual(kinds(report), ["row_moved"])
		self.assertEqual((report["items"][0]["row"], report["items"][0]["old_row"]), (2, 5))

	def test_column_inserted_with_values(self):
		rows = [r[:2] + [v] + r[2:] for r, v in zip(rows_with({(3, 2): "4800"}), ["Bonus", "", "200", "", ""])]
		report = run([(snap(Employees=rows), SARA)])
		self.assertEqual(kinds(report), ["col_added", "cell"])
		self.assertEqual(report["items"][0]["filled"], 1)
		self.assertEqual(report["items"][1]["cells"], [{"col": "Salary", "old": "4500", "new": "4800"}])

	def test_column_removed_and_renamed(self):
		rows = [[r[0], r[2], r[3]] for r in rows_with()]
		rows[0][1] = "Basic Salary"
		report = run([(snap(Employees=rows), AHMED)])
		self.assertEqual(sorted(kinds(report)), ["col_removed", "col_renamed"])
		renamed = [i for i in report["items"] if i["kind"] == "col_renamed"][0]
		self.assertEqual((renamed["old"], renamed["new"]), ("Salary", "Basic Salary"))

	def test_value_in_column_without_header(self):
		rows = rows_with()
		rows[1] += ["", "note"]
		report = run([(snap(Employees=rows), AHMED)])
		self.assertEqual(report["items"][0]["cells"], [{"col": "Column F", "old": "", "new": "note"}])

	def test_blank_row_filled_is_added_not_changed(self):
		base_rows = rows_with()
		base_rows.insert(3, [])
		filled = [list(r) for r in base_rows]
		filled[3] = ["Nadia", "HR", "4100", "Active"]
		report = run([(snap(Employees=filled), AHMED)], base=snap(Employees=base_rows))
		self.assertEqual(kinds(report), ["row_added"])

	def test_ignored_column(self):
		cfg = DiffConfig.build(1, "A", "Status")
		report = run([(snap(Employees=rows_with({(1, 3): "Left", (2, 2): "4100"})), AHMED)], cfg=cfg)
		self.assertEqual(kinds(report), ["cell"])
		self.assertEqual(report["items"][0]["cells"][0]["col"], "Salary")

	def test_no_header_mode_uses_letters(self):
		cfg = DiffConfig.build(0, "A", "")
		report = run([(snap(Employees=rows_with({(2, 2): "6000"})), AHMED)], cfg=cfg)
		self.assertEqual(report["items"][0]["cells"][0]["col"], "Column C")
		self.assertEqual(report["items"][0]["row"], 3)

	def test_two_label_columns(self):
		cfg = DiffConfig.build(1, "A, B", "")
		report = run([(snap(Employees=rows_with({(2, 2): "6000"})), AHMED)], cfg=cfg)
		self.assertEqual(report["items"][0]["label"], "Bilal · HR")

	def test_whole_row_overwritten_in_place(self):
		report = run([(snap(Employees=rows_with({(2, 0): "Zaid", (2, 1): "Sales", (2, 2): "1", (2, 3): "New"})), AHMED)])
		self.assertEqual(kinds(report), ["cell"])
		self.assertEqual(len(report["items"][0]["cells"]), 4)


class TestSessions(unittest.TestCase):
	def test_changes_attributed_to_each_editor(self):
		s1 = snap(Employees=rows_with({(2, 2): "6000"}, extra=[["Zaid", "Sales", "3500", "Active"]]))
		s2 = snap(Employees=rows_with({(2, 2): "6000", (3, 3): "Approved"}, extra=[["Zaid", "Sales", "3500", "Active"]], drop={4}))
		report = run([(s1, AHMED), (s2, SARA)])
		who = {(i["kind"], i.get("label")): by_name(report, i) for i in report["items"]}
		self.assertEqual(who[("cell", "Bilal")], "Ahmed Ali")
		self.assertEqual(who[("cell", "Sara")], "Sara Khan")
		self.assertEqual(who[("row_added", "Zaid")], "Ahmed Ali")
		self.assertEqual(who[("row_removed", "Omar")], "Sara Khan")
		self.assertEqual(report["active_editors"], [0, 1])

	def test_last_editor_of_a_cell_wins(self):
		s1 = snap(Employees=rows_with({(2, 2): "6000"}))
		s2 = snap(Employees=rows_with({(2, 2): "6500"}))
		report = run([(s1, AHMED), (s2, SARA)])
		item = report["items"][0]
		self.assertEqual(item["cells"], [{"col": "Salary", "old": "4000", "new": "6500"}])
		self.assertEqual(by_name(report, item), "Sara Khan")

	def test_undo_sends_nothing(self):
		cfg = DiffConfig.build(1, "A", "")
		base = snap(Employees=BASE_ROWS)
		session = Session.start(base, cfg)
		session.step(snap(Employees=rows_with({(2, 2): "6000"})), AHMED)
		session.step(snap(Employees=BASE_ROWS), AHMED)
		self.assertTrue(session.has_changes)
		self.assertEqual(session.finalize(base)["items"], [])

	def test_formatting_only_save_is_not_a_change(self):
		cfg = DiffConfig.build(1, "A", "")
		session = Session.start(snap(Employees=BASE_ROWS), cfg)
		self.assertFalse(session.step(snap(Employees=BASE_ROWS), AHMED))
		self.assertFalse(session.has_changes)

	def test_deleted_then_retyped_elsewhere_is_a_move(self):
		s1 = snap(Employees=rows_with(drop={2}))
		s2 = snap(Employees=rows_with(drop={2}, extra=[["Bilal", "HR", "4000", "Active"]]))
		report = run([(s1, AHMED), (s2, SARA)])
		self.assertEqual(kinds(report), ["row_moved"])

	def test_insert_then_edit_inserted_row(self):
		rows1 = rows_with()
		rows1.insert(2, ["Nadia", "HR", "4100", "Active"])
		rows2 = [list(r) for r in rows1]
		rows2[2][2] = "4200"
		rows2[5][2] = "3300"
		report = run([(snap(Employees=rows1), AHMED), (snap(Employees=rows2), SARA)])
		added = [i for i in report["items"] if i["kind"] == "row_added"][0]
		cell = [i for i in report["items"] if i["kind"] == "cell"][0]
		self.assertIn(["Salary", "4200"], added["values"])
		self.assertEqual((cell["label"], cell["row"], by_name(report, cell)), ("Omar", 6, "Sara Khan"))

	def test_sheet_added_removed_renamed(self):
		base = snap(Employees=BASE_ROWS, Notes=[["Note"], ["hello"]])
		new = snap(Staff=BASE_ROWS, Budget=[["Item", "Cost"], ["Laptop", "3000"]])
		report = run([(new, AHMED)], base=base)
		found = {(i["kind"], i["sheet"]) for i in report["items"]}
		self.assertEqual(found, {("sheet_renamed", "Staff"), ("sheet_added", "Budget"), ("sheet_removed", "Notes")})

	def test_hidden_sheet_changes_are_skipped(self):
		base = snap(Employees=BASE_ROWS, Lookup=[["Code"], ["A"]])
		base["sheets"]["Lookup"]["hidden"] = True
		new = snap(Employees=BASE_ROWS, Lookup=[["Code"], ["B"]])
		new["sheets"]["Lookup"]["hidden"] = True
		self.assertEqual(run([(new, AHMED)], base=base)["items"], [])

	def test_report_text(self):
		report = run([(snap(Employees=rows_with({(2, 2): "6000"})), AHMED)])
		self.assertEqual(report_text(report), "[Employees] Row 3 · Bilal — Salary: 4000 → 6000 (by Ahmed Ali)")

	def test_large_sheet_is_fast_enough(self):
		import time

		header = ["ID", "Name"] + [f"C{i}" for i in range(18)]
		rows = [header] + [[str(i), f"Person {i}"] + [str(i * j) for j in range(18)] for i in range(3000)]
		new = [list(r) for r in rows]
		new[1500][5] = "changed"
		new.insert(10, ["X", "New person"] + ["1"] * 18)
		started = time.time()
		report = run([(snap(Data=new), AHMED)], base=snap(Data=rows))
		self.assertLess(time.time() - started, 20)
		self.assertEqual(sorted(kinds(report)), ["cell", "row_added"])


if __name__ == "__main__":
	unittest.main()
