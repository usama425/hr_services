# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Site fixes after the v14 -> v15 upgrade. Run after every migrate; idempotent."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

# Fields hr_services relies on that were created on the v14 site (not shipped in
# the app). Created only when missing, so a site's own definition always wins.
SITE_CUSTOM_FIELDS = {
	"Request For Payment": [
		{
			"fieldname": "department",
			"label": "Department",
			"fieldtype": "Link",
			"options": "Department",
			"insert_after": "project_name",
		},
	],
}


def after_migrate():
	remove_broken_link_custom_fields()
	ensure_site_custom_fields()


def remove_broken_link_custom_fields():
	"""Delete Custom Fields that link to a DocType which doesn't exist.

	Frappe fails every form and list that loads such a field ("Missing DocType").
	Seen: Journal Entry-loan_transfer -> "Loan Transfer", left by a Lending
	version that was later replaced. Deleting the Custom Field keeps its column.
	"""
	fields = frappe.get_all(
		"Custom Field",
		filters={"fieldtype": ("in", ("Link", "Table", "Table MultiSelect")), "options": ("is", "set")},
		fields=["name", "dt", "options"],
	)
	existing = set(frappe.get_all("DocType", pluck="name"))
	broken = [f for f in fields if f.options not in existing and f.dt in existing]
	for f in broken:
		frappe.delete_doc("Custom Field", f.name, force=True, ignore_missing=True)
		frappe.clear_cache(doctype=f.dt)

	if broken:
		print(
			"hr_services: removed Custom Fields linking to missing DocTypes:",
			", ".join(f"{f.name} -> {f.options}" for f in broken),
		)


def ensure_site_custom_fields():
	for doctype, fields in SITE_CUSTOM_FIELDS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		for df in fields:
			if meta.has_field(df["fieldname"]) or (
				df.get("options") and not frappe.db.exists("DocType", df["options"])
			):
				continue
			create_custom_field(doctype, df, is_system_generated=False)
			frappe.clear_cache(doctype=doctype)
			print(f"hr_services: restored Custom Field {doctype}-{df['fieldname']}")
