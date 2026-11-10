# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Finish converting ERPNext v14 loans for the Lending app.

When Lending is installed on a site upgraded from v14, its after_install
converts the old data: Loan Type becomes Loan Product (same names), loans get
loan_product from loan_type and repayment schedules are created. If that step
did not run, Loan Product stays empty, every loan has an empty loan_product and
the old tabLoan Type table is left behind. This runs Lending's own conversion
in exactly that state and does nothing on any other site.
"""

import frappe


def execute():
	if "lending" not in frappe.get_installed_apps():
		return
	if not frappe.db.table_exists("Loan Type"):
		return
	if frappe.db.count("Loan Product"):
		return
	if not frappe.db.sql("select count(*) from `tabLoan Type`")[0][0]:
		return

	from lending.install import get_post_install_patches, run_patches

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
