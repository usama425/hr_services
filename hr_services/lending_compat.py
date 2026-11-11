# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Loan Management lives in the separate Lending app since ERPNext v15.

hr_services works without it; only the loan features (employee advances on
Request For Payment, loan reports, loan figures on the Tracker page) need it.
"""

import frappe
from frappe import _

LENDING_DOCTYPES = ("Loan", "Loan Product", "Loan Repayment", "Loan Disbursement")


def has_lending():
	# Check the installed app, not the DocType: sites upgraded from v14 can still
	# have the old ERPNext "Loan" DocType record without the Lending app.
	return "lending" in frappe.get_installed_apps()


def require_lending(feature=None):
	if has_lending():
		return

	frappe.throw(
		_("{0} needs the Lending app. Add the Lending app (version-15) to the bench and install it on this site.").format(
			feature or _("This feature")
		),
		title=_("Lending app not installed"),
	)


# RFP Employee Advances.advance_type links to Loan Product (a Lending DocType).
# Without Lending that link points at a missing DocType and every Request For
# Payment form warns "Field advance_type is referring to non-existing doctype".
# So without Lending it is shown as a plain text field; with Lending it is the
# normal Loan Product link again.
ADVANCE_TYPE = ("RFP Employee Advances", "advance_type")
ADVANCE_TYPE_PROPERTIES = {"fieldtype": ("Data", "Select"), "options": ("", "Text")}


def sync_advance_type_field():
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	doctype, fieldname = ADVANCE_TYPE
	if not frappe.db.exists("DocType", doctype):
		return

	for prop, (value, property_type) in ADVANCE_TYPE_PROPERTIES.items():
		name = f"{doctype}-{fieldname}-{prop}"
		if has_lending():
			frappe.delete_doc("Property Setter", name, ignore_missing=True, force=True)
		else:
			make_property_setter(
				doctype, fieldname, prop, value, property_type, validate_fields_for_doctype=False
			)

	frappe.clear_cache(doctype=doctype)


def after_app_install(app_name):
	if app_name in ("lending", "hr_services"):
		sync_advance_type_field()
	if app_name == "lending":
		# runs after Lending's own after_install
		convert_v14_loans_if_needed()


def after_app_uninstall(app_name):
	if app_name == "lending":
		sync_advance_type_field()


def remove_orphan_loan_doctypes():
	"""Delete leftover ERPNext v14 Loan Management DocType records (records only).

	ERPNext v15's remove_loan_management_module patch deletes these when a v14
	site is upgraded without Lending, but some sites keep (or get back) records
	such as Loan / Loan Type / Loan Repayment. Lending's install then fails with
	"Duplicate entry 'Loan Product'" while converting Loan Type -> Loan Product
	and leaves the site half converted. Deleting a DocType record does not drop
	its table, so all loan data stays and Lending converts it on install.
	"""
	if has_lending():
		return

	orphans = frappe.get_all("DocType", filters={"module": "Loan Management", "custom": 0}, pluck="name")
	for name in orphans:
		frappe.delete_doc("DocType", name, force=True, ignore_missing=True)

	if orphans:
		frappe.db.commit()
		print(f"hr_services: removed leftover v14 Loan Management DocType records: {', '.join(orphans)}")


def before_app_install(app_name):
	if app_name == "lending":
		remove_orphan_loan_doctypes()


def convert_v14_loans_if_needed():
	"""Run Lending's own v14 -> Lending conversion if it was skipped.

	Seen on production: Lending installed, but Loan Product empty, every loan
	with an empty loan_product and the old tabLoan Type table left behind.
	Only acts in exactly that state (Lending installed, tabLoan Type with rows,
	Loan Product empty), so it is a cheap no-op on every other migrate.
	"""
	if not has_lending():
		return
	if not frappe.db.table_exists("Loan Type") or frappe.db.count("Loan Product"):
		return
	if not frappe.db.sql("select count(*) from `tabLoan Type`")[0][0]:
		return

	try:
		from lending.install import get_post_install_patches, run_patches
	except ImportError:
		# Only Lending's version-15 branch converts ERPNext v14 loans; develop /
		# version-16 (built for Frappe 16+) don't. Don't block the migrate.
		msg = (
			"ERPNext v14 loans are not converted yet (Loan Product is empty). The Lending app on "
			"this bench is not the version-15 branch, which is the one that converts them. "
			"Switch Lending to version-15 and migrate again. Do NOT uninstall Lending: that "
			"would delete the loan tables."
		)
		print(f"hr_services: WARNING: {msg}")
		frappe.log_error(title="Lending: v14 loans not converted", message=msg)
		return

	run_patches(get_post_install_patches())
	frappe.db.commit()
	print(
		"hr_services: converted v14 loans for Lending -",
		frappe.db.count("Loan Product"),
		"loan products,",
		frappe.db.count("Loan", {"loan_product": ("is", "set")}),
		"of",
		frappe.db.count("Loan"),
		"loans linked",
	)
