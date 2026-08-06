# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Visibility rules for ERC Task.

A task is visible only to the three parties involved: the person it is assigned
to, the person who assigned it, and anyone CC'd. There is deliberately no blanket
exemption for System Manager - most of the internal users hold that role, so
exempting it would make "only mine" meaningless. Administrator still bypasses
permissions natively, which is how support access works.

Wired up via permission_query_conditions and has_permission in hooks.py.
"""

import frappe


def _is_unrestricted(user):
	return user == "Administrator"


def get_permission_query_conditions(user=None):
	"""SQL fragment limiting the ERC Task list to tasks the user is party to."""
	user = user or frappe.session.user

	if _is_unrestricted(user):
		return ""

	escaped = frappe.db.escape(user)

	return f"""(
		`tabERC Task`.assigned_by = {escaped}
		or `tabERC Task`.owner = {escaped}
		or exists (
			select 1 from `tabERC Task Assignee` a
			where a.parent = `tabERC Task`.name
				and a.parenttype = 'ERC Task'
				and a.user = {escaped}
		)
		or exists (
			select 1 from `tabERC Task CC` cc
			where cc.parent = `tabERC Task`.name
				and cc.parenttype = 'ERC Task'
				and cc.user = {escaped}
		)
	)"""


def has_permission(doc, ptype=None, user=None):
	"""Document-level counterpart, so a direct read cannot bypass the list filter."""
	user = user or frappe.session.user

	if _is_unrestricted(user):
		return True

	# Creating a task is governed by the role alone. `assigned_by` is stamped in
	# before_insert, which runs *after* this check, so on a brand new document
	# there is nothing yet to match the creator against.
	if ptype == "create" or not doc.get("name"):
		return True

	if user in (doc.get("assigned_by"), doc.get("owner")):
		return True

	involved = (doc.get("assignees") or []) + (doc.get("cc_users") or [])

	return any(row.user == user for row in involved)
