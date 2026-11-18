# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Write Job Offers into HR's recruitment Excel sheet in Microsoft 365.

One row per offer, found by the "Job request" column (which holds the Job Offer number). Columns are matched by header
text, so the sheet's column order doesn't matter and unknown columns are left as
they are (Graph ignores null cells in a range update). Runs in the background
after the offer is saved. The system is the source of truth: these cells are
overwritten on every change of the offer.
"""

import re
from urllib.parse import quote

import frappe
import requests
from frappe import _
from frappe.utils import cint, now_datetime

from hr_services.recruitment.tracker import COLUMNS, KEY_HEADER, build_rows, excel_value, get_offers
from hr_services.teams_excel_notify.graph import GRAPH, TIMEOUT, GraphError
from hr_services.teams_excel_notify.tasks import graph_client

SETTINGS = "Recruitment Tracker Settings"
CHUNK = 100
DATE_FORMAT = "[$-en-US]d/mmm/yy;@"  # exactly as the sheet's "Offer Sign DT" column (English months)


def _settings():
	return frappe.get_cached_doc(SETTINGS)


def _norm(text):
	return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _col_letter(index):
	"""0-based column index -> Excel letters."""
	letters = ""
	index += 1
	while index:
		index, rem = divmod(index - 1, 26)
		letters = chr(65 + rem) + letters
	return letters


# --------------------------------------------------------------------------- triggers


def enqueue_offer_sync(offer_name):
	try:
		settings = _settings()
	except frappe.DoesNotExistError:
		return
	if not cint(settings.enabled) or not settings.item_id:
		return
	frappe.enqueue(
		"hr_services.recruitment.excel_sync.sync_offers",
		queue="short",
		job_id=f"recruitment-excel-sync::{offer_name}",
		deduplicate=True,
		enqueue_after_commit=True,
		offer_names=[offer_name],
	)


@frappe.whitelist()
def sync_all():
	frappe.only_for(("HR Manager", "System Manager"))
	frappe.enqueue("hr_services.recruitment.excel_sync._sync_all_job", queue="long", timeout=3600,
				   job_id="recruitment-excel-sync::all", deduplicate=True)


def _sync_all_job():
	# Only offers in the new flow; old offers and their sheet rows are never touched.
	filters = {"docstatus": ["<", 2], "workflow_state": ["not like", "Legacy%"]}
	names = frappe.get_all("Job Offer", filters=filters, pluck="name", order_by="creation asc", limit_page_length=0)
	for i in range(0, len(names), CHUNK):
		sync_offers(names[i : i + CHUNK])


@frappe.whitelist()
def test_connection():
	frappe.only_for(("HR Manager", "System Manager"))
	sheet = Sheet(_settings())
	headers = sheet.read()
	missing = [h for h, *_ in COLUMNS if _norm(h) not in headers]
	msg = _("Connected to <b>{0}</b>, sheet <b>{1}</b>: {2} data row(s).").format(
		_settings().file_name, sheet.worksheet, max(sheet.last_row - sheet.header_row, 0)
	)
	if missing:
		msg += "<br><br>" + _("These columns are not in the sheet and will be skipped:") + "<br>" + ", ".join(missing)
	return msg


def connect_workbook(settings):
	"""Settings validate: resolve the pasted link to the drive item."""
	client = graph_client()
	try:
		item = client.resolve_link(settings.workbook_link)
	except GraphError as exc:
		frappe.throw(str(exc), title=_("Could not open the Excel file"))
	settings.drive_id = (item.get("parentReference") or {}).get("driveId")
	settings.item_id = item.get("id")
	settings.file_name = item.get("name")


# --------------------------------------------------------------------------- sync


def sync_offers(offer_names):
	settings = _settings()
	if not cint(settings.enabled) or not settings.item_id:
		return
	offers = get_offers(names=offer_names)
	offers = [o for o in offers if o.workflow_state and not o.workflow_state.startswith("Legacy")]
	if not offers:
		return
	try:
		sheet = Sheet(settings)
		sheet.read()
		for row in build_rows(offers):
			sheet.write_row(row)
		frappe.db.set_single_value(SETTINGS, {"last_synced_on": now_datetime(), "last_error": None})
	except Exception as exc:
		frappe.db.set_single_value(SETTINGS, "last_error", str(exc)[:1000])
		frappe.log_error(title=_("Recruitment Excel sync failed"), message=frappe.get_traceback())
		frappe.db.commit()
		raise


class Sheet:
	def __init__(self, settings):
		self.client = graph_client()
		self.worksheet = settings.worksheet_name or "Sheet1"
		self.header_row = cint(settings.header_row) or 1
		ws = quote(f"worksheets('{self.worksheet.replace(chr(39), chr(39) * 2)}')", safe="()'")
		self.base = f"{GRAPH}/drives/{quote(settings.drive_id)}/items/{quote(settings.item_id)}/workbook/{ws}"
		self.col_of = {}  # normalised header -> 0-based column index
		self.row_of = {}  # Job Offer name -> 1-based row number
		self.last_row = self.header_row
		self.table = None  # Excel table holding the rows, if the sheet uses one
		self.table_left = self.table_right = self.table_end = None

	def _call(self, method, url, **kwargs):
		for attempt in (1, 2):
			token = self.client.get_token(force=attempt == 2)
			resp = requests.request(method, url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT, **kwargs)
			if resp.status_code != 401:
				break
		if resp.status_code == 403:
			raise GraphError(
				_("Microsoft refused to write to the file. Grant the app the Files.ReadWrite.All application "
				  "permission (admin consent) in Entra ID. [{0}]").format(resp.text[:200]), 403)
		if resp.status_code >= 400:
			raise GraphError(f"Microsoft Graph error {resp.status_code}: {resp.text[:300]}", resp.status_code)
		return resp.json() if resp.content else {}

	def read(self):
		used = self._call("GET", f"{self.base}/usedRange(valuesOnly=true)", params={"$select": "rowIndex,columnIndex,values"})
		top, left, values = used.get("rowIndex", 0), used.get("columnIndex", 0), used.get("values") or []
		header_i = self.header_row - 1 - top
		if header_i < 0 or header_i >= len(values):
			raise GraphError(_("Row {0} of sheet {1} has no headers.").format(self.header_row, self.worksheet))
		for j, text in enumerate(values[header_i]):
			if _norm(text):
				self.col_of.setdefault(_norm(text), left + j)
		key_col = self.col_of.get(_norm(KEY_HEADER))
		if key_col is None:
			raise GraphError(_("Add a column with the header \"{0}\" to the sheet; rows are matched on it.").format(KEY_HEADER))
		for i in range(header_i + 1, len(values)):
			key = str(values[i][key_col - left] or "").strip()
			if key:
				self.row_of[key] = top + i + 1
		self.last_row = max(top + len(values), self.header_row)
		self._find_table()
		return self.col_of

	def _find_table(self):
		"""The Excel table whose header is the header row: new rows must stay inside it."""
		tables = self._call("GET", f"{self.base}/tables", params={"$select": "name"}).get("value") or []
		for t in tables:
			name = t["name"].replace("'", "''")
			rng = self._call("GET", f"{self.base}/tables('{quote(name)}')/range", params={"$select": "rowIndex,columnIndex,rowCount,columnCount"})
			if rng.get("rowIndex", -1) + 1 == self.header_row:
				self.table = name
				self.table_left = rng["columnIndex"]
				self.table_right = rng["columnIndex"] + rng["columnCount"] - 1
				self.table_end = rng["rowIndex"] + rng["rowCount"]  # 1-based last row
				return

	def write_row(self, row):
		cells, date_cols = {}, set()
		for header, fieldname, ftype, *_ in COLUMNS:
			col = self.col_of.get(_norm(header))
			if col is not None:
				cells[col] = excel_value(row.get(fieldname))
				if ftype == "Date":
					date_cols.add(col)
		if not cells:
			return
		first, last = min(cells), max(cells)
		row_no = self.row_of.get(row["job_offer"])
		if not row_no:
			self.last_row += 1
			if self.table and self.last_row > self.table_end:
				# Past the table's last row: grow the table so the new row keeps its formatting.
				values = [[cells.get(c, "") for c in range(self.table_left, self.table_right + 1)]]
				self._call("POST", f"{self.base}/tables('{quote(self.table)}')/rows/add", json={"values": values})
				self.table_end += 1
				self.last_row = self.table_end
				row_no = self.row_of[row["job_offer"]] = self.last_row
			else:
				row_no = self.row_of[row["job_offer"]] = self.last_row
				self._patch_row(row_no, first, last, values=[[cells.get(c) for c in range(first, last + 1)]])
		else:
			self._patch_row(row_no, first, last, values=[[cells.get(c) for c in range(first, last + 1)]])
		# Dates are written as Excel serial numbers; show them as dates like the sheet's own.
		for col in date_cols:
			self._patch_row(row_no, col, col, numberFormat=[[DATE_FORMAT]])

	def _patch_row(self, row_no, first, last, **body):
		# None in "values" = leave that cell as it is
		address = f"{_col_letter(first)}{row_no}:{_col_letter(last)}{row_no}"
		self._call("PATCH", f"{self.base}/range(address='{address}')", json=body)
