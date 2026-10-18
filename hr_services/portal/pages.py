# Copyright (c) 2026, Elite Resources and contributors
# For license information, please see license.txt

"""Page settings shared by the employee portal pages in hr_services/www.

Each page's .py sibling calls set_context so the pages render the same way the
old Web Page records did: full width, no title header, no breadcrumbs, and never
cached (the pages read the CSRF token for their API calls).
"""

PORTAL_PAGES = {
	"attendance-file-detail": "Attendance File Detail",
	"attendance-file-form": "Attendance File Form",
	"attendance-file-list": "Attendance File List",
	"employee-login": "Employee Login",
	"employee-portal-form": "Employee Portal Form",
	"employee-portal-form-detail": "Employee Portal Form Detail",
	"employee-portal-form-list": "Employee Portal Form List",
	"employee-portal-home": "Employee Portal Home",
	"employee-self-service": "Employee Self Service",
	"forgot-password": "Forgot Password",
	"general-request-detail": "General Request Details",
	"general-request-form": "General Request Form",
	"general-request-list": "General Request List",
	"my-profile": "My Profile",
	"my-requests": "My Requests",
	"request-detail": "Request Details",
	"salary-slip-detail": "Salary Slip Detail",
	"submit-request-form": "Submit Request Form",
	"support": "Support",
}


def set_context(context, route):
	context.title = PORTAL_PAGES[route]
	context.full_width = 1
	context.no_header = 1
	context.no_breadcrumbs = 1
	context.no_cache = 1
	return context
