# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Administrator-only guard for the contract expiry notification doctypes.

The role permissions in the doctype JSONs already grant access to the
``Administrator`` role only, but a System Manager with write access on User could
assign that role to themselves. This hook closes that gap by checking the actual
session user, mirroring ``_only_administrator()`` in
``erc_payroll_automation/permission_guard.py``.

Wired up via ``has_permission`` in hooks.py, so it survives bench migrate.
"""

import frappe
from frappe import _


def only_administrator(doc=None, ptype=None, user=None):
	"""Return False for everyone except the Administrator user."""
	return (user or frappe.session.user) == "Administrator"


def throw_unless_administrator():
	"""Hard stop for whitelisted endpoints, which bypass document permissions."""
	if frappe.session.user != "Administrator":
		frappe.throw(
			_("Only Administrator can manage Contract Expiry Notifications."),
			frappe.PermissionError,
		)
