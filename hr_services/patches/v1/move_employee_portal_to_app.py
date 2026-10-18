import frappe
from frappe.website.utils import clear_website_cache

from hr_services.portal.pages import PORTAL_PAGES


def execute():
	# The portal pages now live in hr_services/www. A published Web Page with the
	# same route takes precedence over a www page, so unpublish the old records.
	# They are kept (not deleted) so they can be republished as a fallback.
	frappe.db.set_value(
		"Web Page", {"route": ["in", list(PORTAL_PAGES)], "published": 1}, "published", 0
	)
	clear_website_cache()

	# Re-saving every external user takes a couple of seconds each, too slow to
	# block migrate. The portal no longer depends on their roles, so it is safe
	# for this to finish shortly after the deploy.
	frappe.enqueue(
		"hr_services.portal.access.restrict_existing_external_users",
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
	)
