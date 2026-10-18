# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Data API for the employee web portal (hr_services/www).

External employees are Website Users with only the "Employee Portal User" role,
so they cannot use frappe.client.* on most of the doctypes the portal shows.
Rather than granting that role read access (which would expose every record of
the doctype), these endpoints read with ignore_permissions and scope every query
to the logged-in user's own Employee record on the server. The browser never
decides whose data it gets.

The signatures mirror frappe.client.get / get_list / insert so the portal pages
only had to change the method name.
"""

import json
import re
from urllib.parse import quote

import frappe
from frappe import _
from frappe.model import default_fields

# How each doctype is tied to the logged-in employee. "field" doctypes have a
# link to Employee; "self" is the Employee record itself; Request For Payment
# links employees through its child tables.
SCOPED = {
	"Employee": {"self": True},
	"Request For Payment": {"child_tables": ("items", "advances", "employees")},
	"Employee General Requests": {"field": "employee"},
	"Attendance Files": {"field": "employee"},
	"Employee Portal Form": {"field": "employee"},
	"ERC Forms": {"field": "employee"},
	"Salary Slip": {"field": "employee", "filters": {"docstatus": 1}},
	"Salary Structure Assignment": {"field": "employee", "filters": {"docstatus": 1}},
	"Loan": {"field": "applicant", "filters": {"applicant_type": "Employee"}},
}

# Shared lookup data the request forms need. Only these fields are returned.
REFERENCE = {
	"Item": {"fields": ("name", "item_name", "item_amount", "description")},
	"Loan Type": {
		"fields": ("name", "loan_name", "amount", "description", "custom_descriptiona_arabic", "show_in_portal"),
		"filters": {"show_in_portal": "True"},
	},
}

USER_FIELDS = ("name", "email", "full_name", "first_name", "last_name", "user_image")

INSERTABLE = (
	"Request For Payment",
	"Employee General Requests",
	"Attendance Files",
	"Employee Portal Form",
	"ERC Forms",
)

# Never accepted from the browser on insert.
PROTECTED_FIELDS = (
	"name", "owner", "creation", "modified", "modified_by", "docstatus",
	"idx", "amended_from", "workflow_state",
)

SALARY_SLIP_PRINT_FORMAT = "Payslip ERC"

IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
ORDER_BY = re.compile(r"^`?[A-Za-z0-9_]+`?( (asc|desc))?$", re.IGNORECASE)
OPERATORS = {"=", "!=", "<", ">", "<=", ">=", "in", "not in", "like", "not like", "is", "between"}


def get_session_employee():
	if frappe.session.user == "Guest":
		raise frappe.PermissionError(_("Please log in to access the employee portal."))

	employee = frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user}, ["name", "employee_name"], as_dict=True
	)
	return employee


def _require_employee():
	employee = get_session_employee()
	if not employee:
		frappe.throw(_("No employee record is linked to your account."), frappe.PermissionError)
	return employee


def _check_fieldname(doctype, fieldname):
	meta = frappe.get_meta(doctype)
	if not IDENTIFIER.match(fieldname or ""):
		frappe.throw(_("Invalid field {0}").format(fieldname))
	if fieldname not in default_fields and not meta.has_field(fieldname):
		frappe.throw(_("Invalid field {0}").format(fieldname))


def _parse(value):
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			return value
	return value


def _clean_fields(doctype, fields, allowed=None):
	fields = _parse(fields) or ["name"]
	if isinstance(fields, str):
		fields = [fields]
	cleaned = []
	for f in fields:
		if allowed is not None and f not in allowed:
			continue
		_check_fieldname(doctype, f)
		cleaned.append(f)
	return cleaned or ["name"]


def _clean_filters(doctype, filters):
	filters = _parse(filters) or {}
	if isinstance(filters, list):
		# [[fieldname, operator, value]] or [[doctype, fieldname, operator, value]]
		converted = {}
		for f in filters:
			if len(f) == 4:
				f = f[1:]
			converted[f[0]] = [f[1], f[2]]
		filters = converted
	if not isinstance(filters, dict):
		frappe.throw(_("Invalid filters"))

	for fieldname, value in filters.items():
		_check_fieldname(doctype, fieldname)
		if isinstance(value, (list, tuple)) and (len(value) != 2 or str(value[0]).lower() not in OPERATORS):
			frappe.throw(_("Invalid filter on {0}").format(fieldname))
	return filters


def _scope_filters(doctype, employee):
	"""Filters that restrict doctype to the employee's own records."""
	rule = SCOPED[doctype]
	filters = dict(rule.get("filters") or {})

	if rule.get("self"):
		filters["name"] = employee.name
	elif rule.get("field"):
		filters[rule["field"]] = employee.name
	elif rule.get("child_tables"):
		filters["name"] = ["in", _rfp_names(employee.name) or [""]]
	return filters


def _rfp_names(employee_no):
	"""Requests the employee appears on, plus any they created themselves."""
	meta = frappe.get_meta("Request For Payment")
	names = set(frappe.get_all("Request For Payment", filters={"owner": frappe.session.user}, pluck="name"))
	for table in SCOPED["Request For Payment"]["child_tables"]:
		child = meta.get_field(table).options
		names.update(
			frappe.get_all(
				child,
				filters={"parenttype": "Request For Payment", "employee_no": employee_no},
				pluck="parent",
			)
		)
	return list(names)


def _assert_in_scope(doctype, name, employee):
	if doctype not in SCOPED or not name:
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	filters = _scope_filters(doctype, employee)
	scope_name = filters.pop("name", None)
	if isinstance(scope_name, list):
		allowed = name in scope_name[1]
	else:
		allowed = scope_name in (None, name)

	if not allowed or not frappe.db.exists(doctype, {**filters, "name": name}):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _strip_other_employees(doc, employee):
	"""An RFP can cover several employees; only return the caller's own lines."""
	for table in SCOPED["Request For Payment"]["child_tables"]:
		doc[table] = [row for row in doc.get(table) or [] if row.get("employee_no") == employee.name]
	doc.pop("invoices", None)
	return doc


@frappe.whitelist()
def get_list(doctype, fields=None, filters=None, order_by=None, limit_page_length=20, limit_start=0):
	"""frappe.client.get_list, restricted to the caller's records or to reference data."""
	if doctype == "File":
		filters = _parse(filters) or {}
		if not isinstance(filters, dict):
			frappe.throw(_("Not permitted"), frappe.PermissionError)
		return get_attachments(filters.get("attached_to_doctype"), filters.get("attached_to_name"))

	if doctype in REFERENCE:
		rule = REFERENCE[doctype]
		fields = _clean_fields(doctype, fields, allowed=rule["fields"])
		filters = {**_clean_filters(doctype, filters), **rule.get("filters", {})}
	elif doctype in SCOPED:
		employee = _require_employee()
		fields = _clean_fields(doctype, fields)
		user_filters = _clean_filters(doctype, filters)
		# Scope filters win, so a page can narrow the list but never widen it.
		filters = {**user_filters, **_scope_filters(doctype, employee)}
	else:
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if order_by and not ORDER_BY.match(order_by.strip()):
		frappe.throw(_("Invalid sort order"))

	return frappe.get_all(
		doctype,
		fields=fields,
		filters=filters,
		order_by=order_by or "creation desc",
		limit_start=frappe.utils.cint(limit_start),
		limit_page_length=min(frappe.utils.cint(limit_page_length) or 20, 1000),
	)


@frappe.whitelist()
def get(doctype, name=None, filters=None, fields=None):
	"""frappe.client.get, restricted to the caller's records or to reference data."""
	if doctype == "User":
		return frappe.db.get_value("User", frappe.session.user, USER_FIELDS, as_dict=True)

	if not name and filters:
		# frappe.client.get also accepts filters instead of a name.
		matches = get_list(doctype, filters=filters, limit_page_length=1)
		if not matches:
			frappe.throw(_("Not found"), frappe.DoesNotExistError)
		name = matches[0].name

	if doctype in REFERENCE:
		rule = REFERENCE[doctype]
		values = frappe.db.get_value(
			doctype, {**rule.get("filters", {}), "name": name}, rule["fields"], as_dict=True
		)
		if not values:
			frappe.throw(_("Not found"), frappe.DoesNotExistError)
		return values

	employee = _require_employee()
	_assert_in_scope(doctype, name, employee)
	doc = frappe.get_doc(doctype, name).as_dict()
	if doctype == "Request For Payment":
		_strip_other_employees(doc, employee)
	_link_private_attach_fields(doctype, doc)
	return doc


def _download_url(file_name):
	return "/api/method/hr_services.portal.api.download_file?name=" + quote(file_name)


def _link_private_attach_fields(doctype, doc):
	"""Point private Attach fields at download_file; /private/files needs desk permissions."""
	for df in frappe.get_meta(doctype).get("fields", {"fieldtype": ["in", ["Attach", "Attach Image"]]}):
		url = doc.get(df.fieldname)
		if not url or not url.startswith("/private/"):
			continue
		file_name = frappe.db.get_value(
			"File", {"file_url": url, "attached_to_doctype": doctype, "attached_to_name": doc.name}
		)
		if file_name:
			doc[df.fieldname] = _download_url(file_name)


@frappe.whitelist()
def get_attachments(doctype, name, fields=None):
	"""Files attached to one of the caller's records.

	Private files are served through download_file below, because the portal
	role has no read permission on the records they are attached to.
	"""
	employee = _require_employee()
	_assert_in_scope(doctype, name, employee)

	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_folder": 0},
		fields=["name", "file_name", "file_url", "file_size", "is_private", "creation"],
		order_by="creation desc",
	)
	for f in files:
		if f.is_private:
			f.file_url = _download_url(f.name)
	return files


@frappe.whitelist()
def download_file(name):
	employee = _require_employee()
	file_doc = frappe.get_doc("File", name)
	if not file_doc.attached_to_doctype or not file_doc.attached_to_name:
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	_assert_in_scope(file_doc.attached_to_doctype, file_doc.attached_to_name, employee)

	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"


@frappe.whitelist()
def download_salary_slip(name):
	employee = _require_employee()
	_assert_in_scope("Salary Slip", name, employee)

	frappe.local.response.filename = name.replace("/", "-") + ".pdf"
	frappe.local.response.filecontent = frappe.get_print(
		"Salary Slip", name, SALARY_SLIP_PRINT_FORMAT, as_pdf=True, no_letterhead=0
	)
	frappe.local.response.type = "pdf"


@frappe.whitelist(methods=["POST"])
def insert(doc):
	"""frappe.client.insert for the portal request forms.

	The employee on the document is always the caller, whatever the browser sent.
	"""
	doc = _parse(doc)
	if not isinstance(doc, dict) or doc.get("doctype") not in INSERTABLE:
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	employee = _require_employee()
	doctype = doc["doctype"]
	for field in PROTECTED_FIELDS:
		doc.pop(field, None)

	if doctype == "Request For Payment":
		doc.pop("invoices", None)
		doc.pop("employees", None)
		doc["verified"] = "False"
		for table in ("items", "advances"):
			for row in doc.get(table) or []:
				for field in PROTECTED_FIELDS:
					row.pop(field, None)
				row["employee_no"] = employee.name
				row["employee_name"] = employee.employee_name
	else:
		doc["employee"] = employee.name
		if frappe.get_meta(doctype).has_field("employee_name"):
			doc["employee_name"] = employee.employee_name

	if doctype == "ERC Forms" and doc.get("reference_doctype"):
		if doc["reference_doctype"] != "Employee General Requests":
			frappe.throw(_("Not permitted"), frappe.PermissionError)
		_assert_in_scope("Employee General Requests", doc.get("reference_name"), employee)

	new_doc = frappe.get_doc(doc)
	new_doc.insert(ignore_permissions=True)
	return new_doc.as_dict()
