"""Turn an .xlsx / .xlsm file into a snapshot of cell values (plain strings).

No Frappe imports: only openpyxl, which Frappe v14 already ships with.
"""

from __future__ import annotations

import datetime as _dt
import io
import math

from openpyxl import load_workbook

from hr_services.teams_excel_notify.differ import SNAPSHOT_VERSION

DEFAULT_MAX_CELLS = 500_000


class WorkbookError(Exception):
	"""The file can't be read as a workbook."""


class WorkbookTooLarge(WorkbookError):
	"""More filled cells than the configured limit."""


def _number(value) -> str:
	if isinstance(value, bool):
		return "TRUE" if value else "FALSE"
	if isinstance(value, int):
		return str(value)
	if math.isnan(value) or math.isinf(value):
		return str(value)
	if value.is_integer() and abs(value) < 1e15:
		return str(int(value))
	return format(value, ".10g")


def normalize(value, number_format: str | None = None) -> str:
	"""The text people would recognise for a cell value."""
	if value is None:
		return ""
	if isinstance(value, bool):
		return "TRUE" if value else "FALSE"
	if isinstance(value, _dt.datetime):
		if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
			return value.strftime("%Y-%m-%d")
		return value.strftime("%Y-%m-%d %H:%M" if value.second == 0 else "%Y-%m-%d %H:%M:%S")
	if isinstance(value, _dt.date):
		return value.strftime("%Y-%m-%d")
	if isinstance(value, _dt.time):
		return value.strftime("%H:%M" if value.second == 0 else "%H:%M:%S")
	if isinstance(value, _dt.timedelta):
		return str(value)
	if isinstance(value, (int, float)):
		if number_format and "%" in number_format and not isinstance(value, bool):
			return _number(value * 100) + "%"
		return _number(value)
	return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _trim(row: list) -> list:
	end = len(row)
	while end and not row[end - 1]:
		end -= 1
	return row[:end]


def read_workbook(
	content: bytes,
	*,
	only_sheets=None,
	ignore_formulas: bool = False,
	max_cells: int = DEFAULT_MAX_CELLS,
) -> dict:
	"""Read every worksheet (or only ``only_sheets``) into a snapshot.

	Hidden sheets are read and marked ``hidden`` so un-hiding a sheet is not
	reported as a new sheet; the report skips them unless asked otherwise.
	Formula cells hold their last calculated value, as Excel shows it.
	"""
	try:
		workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	except Exception as exc:  # zipfile.BadZipFile, KeyError, InvalidFileException, ...
		raise WorkbookError(
			"The file could not be opened as an Excel workbook (.xlsx). "
			"Password-protected, .xls and .xlsb files are not supported."
		) from exc
	formulas = None
	if ignore_formulas:
		formulas = load_workbook(io.BytesIO(content), read_only=True, data_only=False)

	wanted = None
	if only_sheets:
		wanted = {str(s).strip().casefold() for s in only_sheets if str(s).strip()}

	sheets, order, total = {}, [], 0
	try:
		for ws in workbook.worksheets:
			name = ws.title
			if wanted is not None and name.strip().casefold() not in wanted:
				continue
			if not hasattr(ws, "iter_rows"):  # chart sheets
				continue
			ws.reset_dimensions()  # trust the cells, not the file's declared size
			formula_rows = None
			if formulas is not None:
				fws = formulas[name]
				fws.reset_dimensions()
				formula_rows = fws.iter_rows()
			rows = []
			for row in ws.iter_rows():
				frow = next(formula_rows, ()) if formula_rows is not None else ()
				values = []
				for idx, cell in enumerate(row):
					value = getattr(cell, "value", None)
					if frow and idx < len(frow) and getattr(frow[idx], "data_type", None) == "f":
						value = None
					values.append(normalize(value, getattr(cell, "number_format", None)))
				values = _trim(values)
				total += len(values)
				if total > max_cells:
					raise WorkbookTooLarge(
						f"The workbook has more than {max_cells:,} filled cells, which is the limit set for comparing it."
					)
				rows.append(values)
			while rows and not rows[-1]:
				rows.pop()
			sheets[name] = {"rows": rows, "hidden": getattr(ws, "sheet_state", "visible") != "visible"}
			order.append(name)
	finally:
		workbook.close()
		if formulas is not None:
			formulas.close()
	return {"v": SNAPSHOT_VERSION, "order": order, "sheets": sheets}
