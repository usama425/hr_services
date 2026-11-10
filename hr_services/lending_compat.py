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
