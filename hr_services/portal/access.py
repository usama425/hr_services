# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Keep external employees on the web portal and out of the desk.

Anyone whose email is not on the company domain is an external employee. They
get the "Employee Portal User" role and nothing with desk access, which makes
Frappe treat them as a Website User and refuse /app.

This has to be enforced on every User save, not fixed once: ERPNext's
Employee.on_update re-adds the "Employee" role (which has desk access) to the
linked user whenever an Employee record is saved, so HR edits, imports and
patches kept turning external users back into System Users.
"""

import frappe

INTERNAL_DOMAIN = "@eliteresources.co"
PORTAL_ROLE = "Employee Portal User"
STANDARD_USERS = ("Administrator", "Guest")


def is_external_user(user):
	if not user or user in STANDARD_USERS:
		return False
	return not user.strip().lower().endswith(INTERNAL_DOMAIN)


def enforce_portal_only(doc, method=None):
	"""User validate hook. Runs after User.validate has set user_type from the roles."""
	if not is_external_user(doc.name):
		return

	desk_roles = set(
		frappe.get_all("Role", filters={"desk_access": 1}, pluck="name")
	)
	doc.set("roles", [r for r in doc.get("roles") if r.role not in desk_roles])

	if PORTAL_ROLE not in [r.role for r in doc.get("roles")]:
		doc.append("roles", {"role": PORTAL_ROLE})

	doc.user_type = "Website User"
	doc.module_profile = None
	doc.role_profile_name = None


def restrict_existing_external_users():
	"""One-off cleanup: re-save every external user so enforce_portal_only applies.

	Saving (rather than a raw SQL update) also logs the change in Version and
	clears the user's cached roles. Desk sessions are logged out so nobody keeps
	an open /app tab.
	"""
	users = frappe.get_all(
		"User",
		filters={"name": ["not in", STANDARD_USERS], "user_type": "System User"},
		pluck="name",
	)
	changed = []
	for name in users:
		if not is_external_user(name):
			continue
		user = frappe.get_doc("User", name)
		user.flags.ignore_permissions = True
		user.save()
		frappe.sessions.clear_sessions(user=name, force=True)
		changed.append(name)

	frappe.db.commit()
	return changed
