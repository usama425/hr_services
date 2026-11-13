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


def before_migrate():
	fill_contract_party_full_name()


def fill_contract_party_full_name():
	"""Fill Contract.party_full_name where ERPNext's patch can't.

	hr_services adds "Job Applicant" as a Contract party type. ERPNext's
	v14_0.update_full_name_in_contract patch reads `<party type>_name` from the
	party, i.e. "job applicant_name", and the v14 -> v15 migrate fails with
	"Unknown column 'job applicant_name'". Fill those rows first so the patch
	skips them (it only touches rows with an empty party_full_name).

	Runs before the schema sync, so on older v14 sites (e.g. ERPNext 14.82)
	the party_full_name column doesn't exist yet: create it as ERPNext v15
	defines it (Data -> varchar(140)). Columns are read from the database,
	not Frappe's cache, which can be stale during a restore.
	"""
	if not _db_has_table("Contract"):
		return
	if not _db_has_column("Contract", "party_full_name"):
		frappe.db.sql_ddl("alter table `tabContract` add column `party_full_name` varchar(140)")

	party_types = frappe.db.sql_list(
		"select distinct party_type from `tabContract` where ifnull(party_full_name, '') = '' and ifnull(party_type, '') != ''"
	)
	for party_type in party_types:
		if party_type == "Job Applicant" and _db_has_column("Job Applicant", "applicant_name"):
			frappe.db.sql(
				"""update `tabContract` c join `tabJob Applicant` j on j.name = c.party_name
				set c.party_full_name = j.applicant_name
				where c.party_type = 'Job Applicant' and ifnull(c.party_full_name, '') = ''"""
			)
		field = party_type.lower() + "_name"  # what ERPNext's patch reads
		if not _db_has_column(party_type, field):
			# the patch would fail on this party type: fall back to the party's ID
			frappe.db.sql(
				"""update `tabContract` set party_full_name = party_name
				where party_type = %s and ifnull(party_full_name, '') = ''""",
				party_type,
			)
	frappe.db.commit()


def _db_has_table(doctype):
	return bool(
		frappe.db.sql(
			"select 1 from information_schema.tables where table_schema = database() and table_name = %s",
			f"tab{doctype}",
		)
	)


def _db_has_column(doctype, column):
	return bool(
		frappe.db.sql(
			"""select 1 from information_schema.columns
			where table_schema = database() and table_name = %s and column_name = %s""",
			(f"tab{doctype}", column),
		)
	)


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
