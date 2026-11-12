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
		fix_converted_loan_data()


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
		# Lending's version-1 / version-16 / develop lines keep the conversion
		# patches but no longer run them on install (and a fresh install marks
		# them as done), so do the same conversion here.
		_convert_v14_loans()
	else:
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


# ---------------------------------------------------------------------------
# v14 -> Lending conversion for Lending lines without get_post_install_patches.
# Same steps as Lending's own v15_0 patches, done on the data so they work after
# the fact (those patches expect to run during the v14 -> v15 migrate).

def fix_converted_loan_data():
	"""Fill gaps left in converted v14 loan data. Idempotent: only empty values.

	- loan_product on rows that had none in v14 (disbursements, salary slip
	  loans, ...), taken from loan_type or from their Loan.
	- Loan Interest Accrual.due_date (new in Lending; v14 had none) =
	  posting_date, as Lending's own update_due_date_in_accruals patch does. A
	  fresh Lending install marks that patch as done without running it, and
	  salary slips only pick up accruals with due_date <= end date, so without
	  this no loan is deducted in payroll. v14 matched accruals on posting_date,
	  so this gives the same deductions as before.
	"""
	if not has_lending():
		return

	_copy_loan_type_to_loan_product()

	if frappe.db.table_exists("Loan Interest Accrual") and "due_date" in _columns("Loan Interest Accrual"):
		frappe.db.sql(
			"update `tabLoan Interest Accrual` set due_date=posting_date where ifnull(due_date, '')=''"
		)


LOAN_STATUS_TO_SCHEDULE_STATUS = {
	"Sanctioned": "Initiated",
	"Partially Disbursed": "Active",
	"Disbursed": "Active",
	"Loan Closure Requested": "Active",
	"Closed": "Closed",
	"Draft": "Draft",
	"Cancelled": "Cancelled",
	"Rejected": "Rejected",
}


def _columns(doctype):
	return set(frappe.db.get_table_columns(doctype))


def _convert_v14_loans():
	_rename_loan_type_to_loan_product()
	_copy_loan_type_to_loan_product()
	_make_repayment_schedules()

	# Lending's own data fixes for v15 schedules / disbursements (idempotent).
	for patch in (
		"lending.patches.v15_0.update_maturity_date",
		"lending.patches.v15_0.loan_repayment_schedule_status_patch",
		"lending.patches.v15_0.loan_disbursement_status_patch",
		"lending.patches.v1_0.link_disbursement_to_legacy_repayment_schedule",
	):
		try:
			execute = frappe.get_attr(f"{patch}.execute")
		except (ImportError, AttributeError):
			continue
		execute()


def _rename_loan_type_to_loan_product():
	# Loan Product is empty here (checked by the caller): put the old Loan Type
	# table in its place, which keeps every row and name, then sync the columns.
	frappe.db.sql_ddl("DROP TABLE `tabLoan Product`")
	frappe.db.sql_ddl("RENAME TABLE `tabLoan Type` TO `tabLoan Product`")

	# customizations and references that still point at "Loan Type"
	frappe.db.sql(
		"""update `tabCustom Field` set dt='Loan Product', name=concat('Loan Product-', fieldname)
		where dt='Loan Type' and not exists (
			select 1 from (select name from `tabCustom Field`) cf where cf.name=concat('Loan Product-', `tabCustom Field`.fieldname))"""
	)
	frappe.db.sql(
		"update `tabProperty Setter` set doc_type='Loan Product', name=replace(name, 'Loan Type-', 'Loan Product-') where doc_type='Loan Type'"
	)
	frappe.db.sql("update `tabCustom DocPerm` set parent='Loan Product' where parent='Loan Type'")
	frappe.db.sql("update `tabCustom Field` set options='Loan Product' where options='Loan Type' and fieldtype='Link'")
	frappe.db.sql(
		"update `tabProperty Setter` set value='Loan Product' where property='options' and value='Loan Type'"
	)
	for doctype, field in (
		("File", "attached_to_doctype"),
		("Comment", "reference_doctype"),
		("Version", "ref_doctype"),
		("Communication", "reference_doctype"),
	):
		frappe.db.sql(f"update `tab{doctype}` set `{field}`='Loan Product' where `{field}`='Loan Type'")

	frappe.reload_doc("loan_management", "doctype", "loan_product", force=True)
	frappe.clear_cache(doctype="Loan Product")

	cols = _columns("Loan Product")
	if "loan_name" in cols:
		frappe.db.sql("update `tabLoan Product` set product_name=loan_name where ifnull(product_name, '')=''")
	frappe.db.sql("update `tabLoan Product` set product_code=name where ifnull(product_code, '')=''")
	frappe.db.sql("update `tabLoan Product` set docstatus=0")

	# new accounts, as Lending's update_loan_types / update_loan_product_accounts
	for target, source in (
		("interest_receivable_account", "loan_account"),
		("charges_receivable_account", "loan_account"),
		("penalty_receivable_account", "loan_account"),
		("suspense_interest_income", "interest_income_account"),
		("suspense_collection_account", "payment_account"),
		("interest_accrued_account", "interest_income_account"),
		("penalty_accrued_account", "penalty_income_account"),
	):
		if target in cols and source in cols:
			frappe.db.sql(
				f"update `tabLoan Product` set `{target}`=`{source}` where ifnull(`{target}`, '')=''"
			)


def _copy_loan_type_to_loan_product():
	tables = frappe.db.sql(
		"""select table_name from information_schema.columns
		where table_schema=database() and column_name in ('loan_type', 'loan_product')
		group by table_name having count(*)=2""",
		pluck=True,
	)
	for table in tables:
		frappe.db.sql(
			f"""update `{table}` set loan_product=loan_type
			where ifnull(loan_product, '')='' and ifnull(loan_type, '')!=''"""
		)

	# rows that never had a loan_type (e.g. disbursements): take it from their Loan
	for doctype, link in (
		("Loan Disbursement", "against_loan"),
		("Loan Repayment", "against_loan"),
		("Loan Interest Accrual", "loan"),
		("Loan Repayment Schedule", "loan"),
		("Salary Slip Loan", "loan"),
		("Loan Write Off", "loan"),
		("Loan Refund", "loan"),
		("Loan Balance Adjustment", "loan"),
	):
		if frappe.db.table_exists(doctype) and {"loan_product", link} <= _columns(doctype):
			frappe.db.sql(
				f"""update `tab{doctype}` t join `tabLoan` l on l.name=t.`{link}`
				set t.loan_product=l.loan_product
				where ifnull(t.loan_product, '')='' and ifnull(l.loan_product, '')!=''"""
			)


def _make_repayment_schedules():
	if not frappe.db.table_exists("Repayment Schedule"):
		return

	loan_cols = _columns("Loan")
	schedule_cols = _columns("Loan Repayment Schedule")
	copy = [
		f
		for f in (
			"company",
			"loan_amount",
			"monthly_repayment_amount",
			"posting_date",
			"repayment_method",
			"repayment_periods",
			"repayment_start_date",
			"rate_of_interest",
			"repayment_schedule_type",
			"disbursed_amount",
		)
		if f in loan_cols and f in schedule_cols
	]
	skip_row_fields = {"name", "parent", "parenttype", "parentfield", "idx", "doctype", "owner", "creation", "modified", "modified_by", "docstatus"}
	has_schedule = set(frappe.get_all("Loan Repayment Schedule", filters={"docstatus": ("!=", 2)}, pluck="loan"))

	for loan in frappe.get_all(
		"Loan",
		filters={"is_term_loan": 1, "docstatus": ("!=", 2)},
		fields=["name", "loan_product", "status", *copy],
	):
		if loan.name in has_schedule:
			continue

		rows = frappe.db.get_all(
			"Repayment Schedule", {"parent": loan.name, "parenttype": "Loan"}, "*", order_by="idx"
		)
		schedule = frappe.new_doc("Loan Repayment Schedule")
		schedule.flags.ignore_validate = True  # keep the v14 schedule as it is
		schedule.flags.ignore_links = True
		schedule.flags.ignore_permissions = True
		schedule.flags.ignore_mandatory = True
		schedule.loan = loan.name
		schedule.loan_product = loan.loan_product
		schedule.status = LOAN_STATUS_TO_SCHEDULE_STATUS.get(loan.status)
		for f in copy:
			schedule.set(f, loan.get(f))
		for row in rows:
			schedule.append("repayment_schedule", {k: v for k, v in row.items() if k not in skip_row_fields})
		schedule.submit()
