"""Audited closing balances as on 31-12-2024, shown in reports WITHOUT posting any entry.

The ledger (GL Entry) built since 2021 does not match the audited trial balance, and finance
does not want adjustment entries. ERPNext's reports can take their opening balances from a
"closing snapshot" instead of adding up every GL Entry since 2021:

* Trial Balance, Balance Sheet and financial report templates read the `Account Closing
  Balance` rows of the last submitted Period Closing Voucher (standard ERPNext behaviour).
* The General Ledger report does not, so `patch_general_ledger` teaches it the same thing.

`apply()` stores the audited figures as such a snapshot: one Period Closing Voucher record
(saved as submitted, but its posting never runs, so it creates NO GL Entry) plus one
Account Closing Balance row per account. `undo()` deletes exactly those rows again and the
reports fall back to the raw ledger. Nothing in GL Entry is created, changed or deleted by
either. Side effect while applied: ERPNext treats books as closed up to 31-12-2024, so no
accounting document dated on/before that date can be created, amended or cancelled.

    bench --site <site> execute hr_services.custompy.audited_closing.apply
    bench --site <site> execute hr_services.custompy.audited_closing.status
    bench --site <site> execute hr_services.custompy.audited_closing.undo

To change figures: edit AUDITED_BALANCES below, run undo, then apply.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.utils import flt, getdate

COMPANY = "Elite Resources Center"
PERIOD_START = "2024-01-01"
PERIOD_END = "2024-12-31"
FISCAL_YEAR = "2024"
CLOSING_ACCOUNT = "331 - Current year's Profits - ERC"
MARKER = "[ERC Audited Closing 2024]"

# Audited trial balance 2025 opening (= 31-12-2024 closing). Positive = Debit, negative = Credit.
# Accounts not listed (P&L accounts, VAT Input, "VAT 15%", Local Suppliers, ...) close at 0.
AUDITED_BALANCES = {
	"11010201 - ALINMA - ERC": 779741,
	"11010204 - Alinma Bank 5003 - ERC": 5161,
	"11010202 - ALJAZIRA - ERC": 1747997,
	"11010205 - Aljazira Bank 902 - ERC": 6,
	"11040101 - Prepaid Rent - ERC": 37125,
	"11030101 - Local Customer - ERC": 7977324,
	"11030401 - Employee Advances - ERC": 77209,
	"2230101 - Accrued Expenses - Salaries - ERC": -50116,
	"2230102 - Accrued Expenses - GOSI - ERC": -518263,
	"2230103 - Accrued Expenses - Audit Fees - ERC": -18000,
	"22304 - VAT OUTPUT (FROM SELLING OR CUSTOMERS) - ERC": -4924387,
	"2230109-01 - Accrued ESB - Operations - ERC": -1110150,
	"21203 - Zakat provisions - ERC": -364236,
	"311 - Paidup capital - ERC": -250000,
	"321 - Owner's Current Account - ERC": 10088104,
	CLOSING_ACCOUNT: -13477515,
}


# ---------------------------------------------------------------------------------------
# apply / undo / status
# ---------------------------------------------------------------------------------------


def _find_snapshot(company=COMPANY):
	return frappe.db.get_value(
		"Period Closing Voucher",
		{"company": company, "docstatus": 1, "remarks": ("like", f"{MARKER}%")},
		["name", "period_end_date"],
		as_dict=True,
	)


def _gl_fingerprint():
	return frappe.db.sql(
		"select count(*), coalesce(sum(debit), 0), coalesce(sum(credit), 0), max(modified) from `tabGL Entry`"
	)[0]


def _validate_balances():
	if round(sum(AUDITED_BALANCES.values()), 2) != 0:
		frappe.throw(f"Audited balances do not balance: {sum(AUDITED_BALANCES.values())}")
	for account in AUDITED_BALANCES:
		acc = frappe.db.get_value("Account", account, ["company", "is_group", "report_type"], as_dict=True)
		if not acc:
			frappe.throw(f"Account not found: {account}")
		if acc.company != COMPANY or acc.is_group or acc.report_type != "Balance Sheet":
			frappe.throw(f"Account {account} must be a balance sheet ledger of {COMPANY}")


def apply():
	"""Store the audited 31-12-2024 closing as a report snapshot. Posts no GL Entry."""
	if existing := _find_snapshot():
		print(f"Already applied: {existing.name}. Run undo first to change it.")
		return

	if frappe.db.exists(
		"Period Closing Voucher",
		{"company": COMPANY, "docstatus": 1, "period_end_date": (">=", PERIOD_START)},
	):
		frappe.throw("A real Period Closing Voucher already exists for 2024 or later; not applying.")

	_validate_balances()
	before = _gl_fingerprint()
	currency = frappe.get_cached_value("Company", COMPANY, "default_currency")

	pcv = frappe.new_doc("Period Closing Voucher")
	pcv.update(
		{
			"transaction_date": PERIOD_END,
			"fiscal_year": FISCAL_YEAR,
			"company": COMPANY,
			"period_start_date": PERIOD_START,
			"period_end_date": PERIOD_END,
			"closing_account_head": CLOSING_ACCOUNT,
			"gle_processing_status": "Completed",
			"remarks": f"{MARKER} Audited trial balance as on 31-12-2024, used as the opening of "
			"2025 in reports. Snapshot only: NO GL Entry was posted. Managed by "
			"hr_services.custompy.audited_closing (apply / undo).",
			"docstatus": 1,
		}
	)
	pcv.name = make_autoname("ACC-PCV-.YYYY.-.#####", "Period Closing Voucher", pcv)
	pcv.db_insert()  # direct insert: no validate/on_submit, so no closing GL entries are made

	for account, balance in AUDITED_BALANCES.items():
		amount = abs(flt(balance, 2))
		debit, credit = (amount, 0) if balance > 0 else (0, amount)
		frappe.get_doc(
			{
				"doctype": "Account Closing Balance",
				"closing_date": PERIOD_END,
				"account": account,
				"company": COMPANY,
				"account_currency": currency,
				"debit": debit,
				"credit": credit,
				"debit_in_account_currency": debit,
				"credit_in_account_currency": credit,
				"debit_in_reporting_currency": debit,
				"credit_in_reporting_currency": credit,
				"reporting_currency_exchange_rate": 1,
				"is_period_closing_voucher_entry": 0,
				"period_closing_voucher": pcv.name,
			}
		).db_insert()

	after = _gl_fingerprint()
	if before != after:
		frappe.db.rollback()
		frappe.throw("GL Entry changed while applying; rolled back.")

	frappe.db.commit()
	frappe.clear_cache()
	print(f"Applied {pcv.name}: {len(AUDITED_BALANCES)} audited balances. GL Entry untouched {after[:3]}.")


def undo():
	"""Remove the snapshot again. Reports go back to the raw ledger. Touches no GL Entry."""
	snap = _find_snapshot()
	if not snap:
		print("Nothing to undo: no audited closing snapshot found.")
		return

	if frappe.db.exists("GL Entry", {"voucher_type": "Period Closing Voucher", "voucher_no": snap.name}):
		frappe.throw(f"{snap.name} has GL entries, so it is not the report-only snapshot. Not touching it.")

	before = _gl_fingerprint()
	rows = frappe.db.count("Account Closing Balance", {"period_closing_voucher": snap.name})
	frappe.db.delete("Account Closing Balance", {"period_closing_voucher": snap.name})
	frappe.db.delete("Comment", {"reference_doctype": "Period Closing Voucher", "reference_name": snap.name})
	frappe.db.delete("Period Closing Voucher", {"name": snap.name})

	if _gl_fingerprint() != before:
		frappe.db.rollback()
		frappe.throw("GL Entry changed while undoing; rolled back.")

	frappe.db.commit()
	frappe.clear_cache()
	print(f"Undone: removed {snap.name} and its {rows} closing balance rows. GL Entry untouched {before[:3]}.")


def status():
	snap = _find_snapshot()
	if not snap:
		print("Not applied. Reports use the raw ledger.")
		return
	rows = frappe.get_all(
		"Account Closing Balance",
		filters={"period_closing_voucher": snap.name},
		fields=["account", "debit", "credit"],
		order_by="account",
	)
	print(f"Applied: {snap.name} (closing {snap.period_end_date})")
	for r in rows:
		print(f"  {r.account:55} Dr {r.debit:>15,.2f}  Cr {r.credit:>15,.2f}")
	print(f"  {'Total':55} Dr {sum(r.debit for r in rows):>15,.2f}  Cr {sum(r.credit for r in rows):>15,.2f}")


def protect_snapshot(doc, method=None):
	"""doc_events guard: the snapshot may only be removed through undo()."""
	if (doc.remarks or "").startswith(MARKER):
		frappe.throw(
			_(
				"{0} holds the audited closing balances of 2024 (no GL entries). "
				"Ask the system administrator to remove it with the audited_closing undo command."
			).format(doc.name),
			title=_("Audited Closing"),
		)


# ---------------------------------------------------------------------------------------
# General Ledger report
# ---------------------------------------------------------------------------------------


def _get_snapshot_balances(company):
	cache = getattr(frappe.local, "erc_audited_snapshot", None)  # per request / job
	if cache is None:
		cache = frappe.local.erc_audited_snapshot = {}
	if company not in cache:
		snap = _find_snapshot(company)
		if snap:
			snap.balances = defaultdict(float)
			for r in frappe.get_all(
				"Account Closing Balance",
				filters={"period_closing_voucher": snap.name},
				fields=["account", "debit", "credit"],
			):
				snap.balances[r.account] += flt(r.debit) - flt(r.credit)
		cache[company] = snap
	return cache[company]


# Filters under which the snapshot (one total per account, no party/cost center/project) cannot
# be applied faithfully: the report then shows the raw ledger exactly as before.
_UNSUPPORTED_FILTERS = (
	"party",
	"party_type",
	"cost_center",
	"project",
	"voucher_no",
	"against_voucher_no",
	"finance_book",
	"show_cancelled_entries",
	"show_opening_entries",
	"disable_opening_balance_calculation",
	"include_dimensions",
)


def _adjust_gl_entries(filters, gl_entries):
	snap = _get_snapshot_balances(filters.get("company"))
	if not snap:
		return gl_entries

	end = getdate(snap.period_end_date)
	from_date, to_date = getdate(filters.from_date), getdate(filters.to_date)
	if to_date < end:
		return gl_entries

	by_account = filters.get("categorize_by") == "Categorize by Account"
	if not (filters.get("account") or by_account):
		return gl_entries  # without an account the report computes no opening at all
	if any(filters.get(f) for f in _UNSUPPORTED_FILTERS):
		return gl_entries
	if filters.get("categorize_by") == "Categorize by Party":
		return gl_entries
	company_currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	if filters.get("presentation_currency") and filters.presentation_currency != company_currency:
		return gl_entries
	for dim in frappe.get_all("Accounting Dimension", filters={"disabled": 0}, pluck="fieldname"):
		if filters.get(dim):
			return gl_entries

	leaf_accounts = dict(
		frappe.get_all(
			"Account",
			filters={"company": filters.company, "is_group": 0},
			fields=["name", "report_type"],
			as_list=True,
		)
	)
	if filters.get("account"):
		scope = {a for a in filters.account if a in leaf_accounts}  # already expanded to children
	else:
		scope = set(leaf_accounts)

	raw = defaultdict(float)
	for g in gl_entries:
		if g.account in scope and getdate(g.posting_date) <= end:
			raw[g.account] += flt(g.debit) - flt(g.credit)

	if from_date > end:
		# Opening: replace everything up to 31-12-2024 by the audited balance (P&L accounts
		# open at 0 as after a normal year closing). 2025+ movements stay as they are.
		label = _("Audited closing balance as on {0} (report only, not posted)")
		targets = {a: snap.balances.get(a, 0) for a in scope}
		gl_entries = [g for g in gl_entries if not (g.account in scope and getdate(g.posting_date) <= end)]
	else:
		# Period runs through 31-12-2024: add one adjustment line per balance sheet account so
		# that the balance on 31-12-2024 equals the audited figure. P&L accounts stay raw.
		label = _("Audit adjustment to audited closing as on {0} (report only, not posted)")
		targets = {
			a: snap.balances.get(a, 0) - raw[a] for a in scope if leaf_accounts.get(a) == "Balance Sheet"
		}

	label = label.format(frappe.format(end, {"fieldtype": "Date"}))
	extra = []
	for account in sorted(targets):
		amount = flt(targets[account], 2)
		if not amount:
			continue
		debit, credit = (amount, 0) if amount > 0 else (0, -amount)
		extra.append(
			frappe._dict(
				gl_entry=None,
				posting_date=end,
				account=account,
				party_type=None,
				party=None,
				party_name=None,
				voucher_type="Period Closing Voucher",
				voucher_subtype=None,
				voucher_no=snap.name,
				cost_center=None,
				project=None,
				against_voucher_type=None,
				against_voucher=None,
				account_currency=company_currency,
				against="",
				is_opening="No",
				creation=None,
				debit=debit,
				credit=credit,
				debit_in_account_currency=debit,
				credit_in_account_currency=credit,
				debit_in_transaction_currency=debit,
				credit_in_transaction_currency=credit,
				transaction_currency=company_currency,
				remarks=label,
			)
		)

	if not extra and from_date <= end:
		return gl_entries

	gl_entries = gl_entries + extra
	if by_account:
		gl_entries.sort(key=lambda g: (g.account, getdate(g.posting_date)))
	else:
		gl_entries.sort(key=lambda g: getdate(g.posting_date))
	return gl_entries


def patch_general_ledger(*args, **kwargs):
	"""before_request / before_job hook: wrap General Ledger's GL fetch once per process."""
	from erpnext.accounts.report.general_ledger import general_ledger

	if getattr(general_ledger.get_gl_entries, "erc_audited_closing", False):
		return

	original = general_ledger.get_gl_entries

	def get_gl_entries(filters, accounting_dimensions):
		gl_entries = original(filters, accounting_dimensions)
		try:
			return _adjust_gl_entries(filters, gl_entries)
		except Exception:
			frappe.log_error(title="Audited closing: General Ledger adjustment failed")
			return gl_entries

	get_gl_entries.erc_audited_closing = True
	general_ledger.get_gl_entries = get_gl_entries
