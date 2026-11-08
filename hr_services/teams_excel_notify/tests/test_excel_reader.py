"""Tests for reading .xlsx files into snapshots (needs openpyxl only)."""

import datetime as dt
import io
import unittest

from openpyxl import Workbook

from hr_services.teams_excel_notify.excel_reader import WorkbookError, WorkbookTooLarge, normalize, read_workbook


def make_xlsx(build) -> bytes:
	wb = Workbook()
	build(wb)
	out = io.BytesIO()
	wb.save(out)
	return out.getvalue()


class TestNormalize(unittest.TestCase):
	def test_values(self):
		self.assertEqual(normalize(None), "")
		self.assertEqual(normalize(5000.0), "5000")
		self.assertEqual(normalize(0.1 + 0.2), "0.3")
		self.assertEqual(normalize(12), "12")
		self.assertEqual(normalize(True), "TRUE")
		self.assertEqual(normalize(dt.datetime(2026, 11, 7)), "2026-11-07")
		self.assertEqual(normalize(dt.datetime(2026, 11, 7, 9, 30)), "2026-11-07 09:30")
		self.assertEqual(normalize(dt.time(9, 30)), "09:30")
		self.assertEqual(normalize(0.15, "0%"), "15%")
		self.assertEqual(normalize("  Bilal \r\n"), "Bilal")


class TestReadWorkbook(unittest.TestCase):
	def test_basic_sheet(self):
		def build(wb):
			ws = wb.active
			ws.title = "Employees"
			ws.append(["Name", "Salary", "Joined", "Share"])
			ws.append(["Ahmed", 5000, dt.date(2024, 1, 15), 0.25])
			ws["D2"].number_format = "0%"
			ws.append(["Bilal", 4000.5])
			ws["F10"] = None  # styled but empty cells must not create rows

		snap = read_workbook(make_xlsx(build))
		self.assertEqual(snap["order"], ["Employees"])
		rows = snap["sheets"]["Employees"]["rows"]
		self.assertEqual(rows[0], ["Name", "Salary", "Joined", "Share"])
		self.assertEqual(rows[1], ["Ahmed", "5000", "2024-01-15", "25%"])
		self.assertEqual(rows[2], ["Bilal", "4000.5"])
		self.assertEqual(len(rows), 3)

	def test_gaps_are_kept_in_place(self):
		def build(wb):
			ws = wb.active
			ws["A1"] = "Header"
			ws["C5"] = "x"

		rows = read_workbook(make_xlsx(build))["sheets"]["Sheet"]["rows"]
		self.assertEqual(len(rows), 5)
		self.assertEqual(rows[4], ["", "", "x"])
		self.assertEqual(rows[1], [])

	def test_hidden_and_selected_sheets(self):
		def build(wb):
			wb.active.title = "Visible"
			wb.active["A1"] = "v"
			hidden = wb.create_sheet("Lookup")
			hidden["A1"] = "h"
			hidden.sheet_state = "hidden"
			wb.create_sheet("Other")["A1"] = "o"

		content = make_xlsx(build)
		snap = read_workbook(content)
		self.assertEqual(snap["order"], ["Visible", "Lookup", "Other"])
		self.assertTrue(snap["sheets"]["Lookup"]["hidden"])
		self.assertFalse(snap["sheets"]["Visible"]["hidden"])
		only = read_workbook(content, only_sheets=["other", " Visible "])
		self.assertEqual(only["order"], ["Visible", "Other"])

	def test_formula_cells_can_be_ignored(self):
		def build(wb):
			ws = wb.active
			ws.append(["A", "B", "Total"])
			ws.append([1, 2, "=A2+B2"])

		content = make_xlsx(build)
		self.assertEqual(read_workbook(content, ignore_formulas=True)["sheets"]["Sheet"]["rows"][1], ["1", "2"])

	def test_limits_and_bad_files(self):
		def build(wb):
			for r in range(50):
				wb.active.append([r] * 10)

		with self.assertRaises(WorkbookTooLarge):
			read_workbook(make_xlsx(build), max_cells=100)
		with self.assertRaises(WorkbookError):
			read_workbook(b"this is not a workbook")


if __name__ == "__main__":
	unittest.main()
