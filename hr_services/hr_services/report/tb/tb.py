# Copy of ERPNext "Trial Balance" report.
# Only difference: from OPENING_DATE onwards, the opening balance is taken from the
# GL report's OPENING_BALANCES (plus real entries from OPENING_DATE up to the day
# before From Date) instead of the GL Entries posted before OPENING_DATE.
# With a Cost Center / Project / dimension filter, the fixed figures are not used
# (they are per account only), so the opening is just those real entries.

import frappe
from frappe.utils import flt, getdate

import erpnext
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
from erpnext.accounts.report.financial_statements import (
	filter_accounts,
	filter_out_zero_value_rows,
	set_gl_entries_by_account,
)
from erpnext.accounts.report.trial_balance import trial_balance
from erpnext.accounts.report.trial_balance.trial_balance import (
	accumulate_values_into_parents,
	calculate_values,
	get_columns,
	get_opening_balance,
	prepare_data,
	validate_filters,
)

from hr_services.hr_services.report.gl.gl import OPENING_BALANCES, OPENING_DATE


def execute(filters=None):
	validate_filters(filters)
	data = get_data(filters)
	columns = get_columns()
	return columns, data


def get_data(filters):
	accounts = frappe.db.sql(
		"""select name, account_number, parent_account, account_name, root_type, report_type, lft, rgt

		from `tabAccount` where company=%s order by lft""",
		filters.company,
		as_dict=True,
	)
	company_currency = filters.presentation_currency or erpnext.get_company_currency(filters.company)

	if not accounts:
		return None

	accounts, accounts_by_name, parent_children_map = filter_accounts(accounts)

	min_lft, max_rgt = frappe.db.sql(
		"""select min(lft), max(rgt) from `tabAccount`
		where company=%s""",
		(filters.company,),
	)[0]

	gl_entries_by_account = {}

	opening_balances = get_opening_balances(filters)

	# add filter inside list so that the query in financial_statements.py doesn't break
	if filters.project:
		filters.project = [filters.project]

	set_gl_entries_by_account(
		filters.company,
		filters.from_date,
		filters.to_date,
		min_lft,
		max_rgt,
		filters,
		gl_entries_by_account,
		ignore_closing_entries=not flt(filters.with_period_closing_entry),
		ignore_opening_entries=True,
	)

	calculate_values(accounts, gl_entries_by_account, opening_balances, filters.get("show_net_values"))
	accumulate_values_into_parents(accounts, accounts_by_name)

	data = prepare_data(accounts, filters, parent_children_map, company_currency)
	data = filter_out_zero_value_rows(
		data, parent_children_map, show_zero_values=filters.get("show_zero_values")
	)

	return data


def use_custom_opening(filters):
	return filters.company in OPENING_BALANCES and getdate(filters.from_date) >= getdate(OPENING_DATE)


def get_opening_balances(filters):
	if not use_custom_opening(filters):
		return trial_balance.get_opening_balances(filters)

	opening = frappe._dict()

	def add(account, debit, credit):
		row = opening.setdefault(account, {"account": account, "opening_debit": 0.0, "opening_credit": 0.0})
		row["opening_debit"] += flt(debit)
		row["opening_credit"] += flt(credit)

	if not has_dimension_filter(filters):
		for account_number, (debit, credit) in OPENING_BALANCES[filters.company].items():
			account = frappe.db.get_value(
				"Account", {"company": filters.company, "account_number": account_number}, "name"
			)
			if account:
				add(account, debit, credit)

	# real entries from OPENING_DATE up to the day before From Date
	if getdate(filters.from_date) > getdate(OPENING_DATE):
		accounting_dimensions = get_accounting_dimensions(as_list=False)
		for report_type in ("Balance Sheet", "Profit and Loss"):
			for d in get_opening_balance(
				"GL Entry", filters, report_type, accounting_dimensions, start_date=OPENING_DATE
			):
				add(d.account, d.debit, d.credit)

	return opening


def has_dimension_filter(filters):
	if filters.get("cost_center") or filters.get("project"):
		return True

	return any(
		filters.get(dimension.fieldname) for dimension in get_accounting_dimensions(as_list=False)
	)
