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


class ExpiryNoticePage(BaseRenderer):
	def can_render(self):
		conf = frappe.local.conf or {}
		return self.path == "message" and bool(conf.get("maintenance_mode")) and bool(conf.get("show_expiry_notice"))

	def render(self):
		with open(TEMPLATE, encoding="utf-8") as f:
			html = f.read()
		html = html.replace("{{ site }}", escape(frappe.local.site or ""))
		headers = {"Cache-Control": "no-store, max-age=0", "Retry-After": "3600"}
		return self.build_response(html, http_status_code=503, headers=headers)
