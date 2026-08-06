# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Idempotent setup for the ERC Task system.

Safe to re-run: everything checks before it writes, and nothing is ever deleted.

The role granting deserves a note. Frappe's User controller calls
``populate_role_profile_roles()`` on every save, which does ``self.set("roles", [])``
and repopulates from the Role Profile. So for a user who has a Role Profile,
appending a role directly to the User is silently discarded on the next save.
``grant_task_role`` therefore routes those users through their Role Profile and
only appends directly for users who have no profile.
"""

import frappe

ROLE = "ERC Task User"
INTERNAL_DOMAIN = "@eliteresources.co"

DEFAULT_ALLOWED_DOCTYPES = [
	"Sales Invoice",
	"Purchase Invoice",
	"Request For Payment",
	"Employee",
	"Project",
	"Contract",
	"PO Management",
	"Job Applicant",
	"Job Opening",
	"Job Requisition",
	"Payroll Entry",
	"Customer",
	"Supplier",
]


def ensure_role():
	"""Create the ERC Task User role if it is missing."""
	if frappe.db.exists("Role", ROLE):
		return False

	frappe.get_doc(
		{"doctype": "Role", "role_name": ROLE, "desk_access": 1, "is_custom": 1}
	).insert(ignore_permissions=True)

	return True


def get_internal_users():
	"""Enabled System Users on the company domain - the people who use tasks."""
	return frappe.db.sql_list(
		"""
		select name from `tabUser`
		where enabled = 1 and user_type = 'System User' and name like %s
		order by name
		""",
		f"%{INTERNAL_DOMAIN}",
	)


def _role_profile_has_role(profile):
	return frappe.db.exists(
		"Has Role", {"parent": profile, "parenttype": "Role Profile", "role": ROLE}
	)


def grant_task_role(users=None):
	"""Give every internal user the task role, honouring Role Profiles.

	Returns a summary of what changed, so a re-run visibly reports "nothing to do".
	"""
	ensure_role()

	users = users or get_internal_users()
	direct, via_profile, already = [], [], []
	profiles_touched = set()

	for user in users:
		if ROLE in frappe.get_roles(user):
			already.append(user)
			continue

		profile = frappe.db.get_value("User", user, "role_profile_name")

		if profile:
			# Appending to the User would be wiped on its next save.
			if not _role_profile_has_role(profile):
				doc = frappe.get_doc("Role Profile", profile)
				doc.append("roles", {"role": ROLE})
				doc.save(ignore_permissions=True)
				profiles_touched.add(profile)
			via_profile.append(user)
		else:
			doc = frappe.get_doc("User", user)
			doc.append("roles", {"role": ROLE})
			doc.save(ignore_permissions=True)
			direct.append(user)

	frappe.db.commit()

	return {
		"granted_directly": direct,
		"granted_via_role_profile": via_profile,
		"role_profiles_updated": sorted(profiles_touched),
		"already_had_role": already,
	}


def seed_settings(restrict_to_site=None, enabled=0):
	"""Fill ERC Task Settings with the default allow-list, without clobbering edits."""
	settings = frappe.get_single("ERC Task Settings")

	existing = {row.document_type for row in settings.allowed_doctypes or []}
	added = []

	for doctype in DEFAULT_ALLOWED_DOCTYPES:
		if doctype in existing or not frappe.db.exists("DocType", doctype):
			continue
		settings.append("allowed_doctypes", {"document_type": doctype})
		added.append(doctype)

	if restrict_to_site is not None:
		settings.restrict_to_site = restrict_to_site

	settings.enabled = enabled
	settings.notify_on_due_date = 1
	settings.notify_on_completion = 1
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	return {"added_doctypes": added, "total_allowed": len(settings.allowed_doctypes or [])}


def ensure_kanban_board(board_name="ERC Tasks"):
	"""Create a status Kanban for ERC Task if one does not exist."""
	if frappe.db.exists("Kanban Board", board_name):
		return False

	board = frappe.get_doc(
		{
			"doctype": "Kanban Board",
			"kanban_board_name": board_name,
			"reference_doctype": "ERC Task",
			"field_name": "status",
			"private": 0,
			"columns": [
				{"column_name": status, "status": "Active", "indicator": indicator}
				for status, indicator in (
					("Open", "Orange"),
					("In Progress", "Blue"),
					("Completed", "Green"),
					("Cancelled", "Gray"),
				)
			],
		}
	)
	board.insert(ignore_permissions=True)
	frappe.db.commit()

	return True


def setup_all(restrict_to_site=None, enabled=0):
	"""Everything needed to stand the task system up on a site."""
	return {
		"role_created": ensure_role(),
		"roles": grant_task_role(),
		"settings": seed_settings(restrict_to_site=restrict_to_site, enabled=enabled),
		"kanban_created": ensure_kanban_board(),
	}
