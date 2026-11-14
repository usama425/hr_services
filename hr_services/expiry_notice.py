"""Custom page shown instead of Frappe's "Updating" page while a site is in maintenance mode.

Turn it on for a site with:

    bench --site <site> set-config show_expiry_notice 1
    bench --site <site> set-maintenance-mode on

and off again with:

    bench --site <site> set-maintenance-mode off
    bench --site <site> set-config show_expiry_notice 0

The notice only appears when *both* settings are on, so `bench migrate` (which also
switches maintenance mode on while it runs) still shows Frappe's normal "Updating" page.

How it works: when a request arrives during maintenance mode, Frappe renders the
"message" page through the website renderer chain, which checks the `page_renderer`
hook first. This renderer claims that page and returns a self-contained HTML file
that needs no database, cache or external assets.
"""

import os
from html import escape

import frappe
from frappe.website.page_renderers.base_renderer import BaseRenderer

TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "expiry_notice.html")
MOVED_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "site_moved.html")


def moved_to():
	"""The new address set with `bench --site <site> set-config site_moved_to <url>`, if any.

	While it is set, every request to this site gets a "We've upgraded our ERP" page
	with a button that opens the new address in a new tab.
	"""
	url = ((frappe.local.conf or {}).get("site_moved_to") or "").strip()
	return url if url.startswith(("https://", "http://")) else None


class ExpiryNoticePage(BaseRenderer):
	def can_render(self):
		conf = frappe.local.conf or {}
		if self.path != "message":
			return False
		if moved_to():
			return True
		if conf.get("maintenance_mode") and conf.get("show_expiry_notice"):
			return True
		from hr_services.scheduled_maintenance import is_locked

		return is_locked()

	def render(self):
		url = moved_to()
		if url:
			with open(MOVED_TEMPLATE, encoding="utf-8") as f:
				html = f.read()
			html = html.replace("{{ url }}", escape(url, quote=True))
			html = html.replace("{{ url_text }}", escape(url.split("://", 1)[-1].rstrip("/")))
			return self.build_response(html, http_status_code=503, headers={"Cache-Control": "no-store, max-age=0"})
		with open(TEMPLATE, encoding="utf-8") as f:
			html = f.read()
		html = html.replace("{{ site }}", escape(frappe.local.site or ""))
		headers = {"Cache-Control": "no-store, max-age=0", "Retry-After": "3600"}
		return self.build_response(html, http_status_code=503, headers=headers)
