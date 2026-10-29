# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Visibility rules for Print Format.

Every desk user may read Print Formats (via a "Desk User" role permission), but
only those for document types they can read themselves: someone without access
to Payment Entry does not see the Payment Entry print formats. Changing print
formats is still limited to System Manager by the role permissions.

Wired up via permission_query_conditions and has_permission in hooks.py.
"""

import frappe


def _is_unrestricted(user):
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def get_permission_query_conditions(user=None):
	"""SQL fragment limiting the Print Format list to readable document types."""
	user = user or frappe.session.user

	if _is_unrestricted(user):
		return ""

	from frappe.utils.user import UserPermissions

	readable = frappe.get_user().get_can_read() if user == frappe.session.user else UserPermissions(user).get_can_read()
	if not readable:
		return "1=0"

	return "`tabPrint Format`.doc_type in ({})".format(", ".join(frappe.db.escape(dt) for dt in readable))


def has_permission(doc, ptype=None, user=None):
	"""Document-level counterpart, so a direct read cannot bypass the list filter."""
	user = user or frappe.session.user

	if _is_unrestricted(user):
		return True

	return bool(doc.doc_type) and frappe.has_permission(doc.doc_type, "read", user=user)
