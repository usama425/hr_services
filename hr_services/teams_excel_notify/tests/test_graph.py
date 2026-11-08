"""Tests for the Microsoft Graph client, with a fake HTTP session (no network)."""

import base64
import datetime as dt
import json
import unittest

from hr_services.teams_excel_notify.graph import (
	GraphClient,
	GraphError,
	_MemoryCache,
	check_excel_item,
	editor_from_item,
	encode_share_url,
	parse_graph_time,
)


class FakeResponse:
	def __init__(self, status=200, payload=None, content=b""):
		self.status_code = status
		self._payload = payload
		self.content = content
		self.text = json.dumps(payload) if payload is not None else ""

	def json(self):
		if self._payload is None:
			raise ValueError("no json")
		return self._payload

	def iter_content(self, chunk_size=1):
		for i in range(0, len(self.content), chunk_size):
			yield self.content[i : i + chunk_size]

	def close(self):
		pass


def fake_token(roles):
	body = base64.urlsafe_b64encode(json.dumps({"roles": roles}).encode()).decode().rstrip("=")
	return f"header.{body}.signature"


class FakeSession:
	def __init__(self, routes, token_response=None):
		self.routes = routes  # list of (method, url-substring, response)
		self.token_response = token_response or FakeResponse(200, {"access_token": fake_token(["Files.Read.All"]), "expires_in": 3599})
		self.calls = []

	def post(self, url, data=None, timeout=None):
		self.calls.append(("POST", url))
		return self.token_response

	def request(self, method, url, params=None, headers=None, timeout=None, stream=False):
		self.calls.append((method, url))
		for m, part, response in self.routes:
			if m == method and part in url:
				return response
		return FakeResponse(404, {"error": {"code": "itemNotFound", "message": "nope"}})


def client(session):
	return GraphClient("tenant", "11111111-2222-3333-4444-555555555555", "secret", cache=_MemoryCache(), session=session)


EXCEL_ITEM = {
	"id": "ITEM1",
	"name": "Tracker.xlsx",
	"file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
	"parentReference": {"driveId": "b!DRIVE"},
	"webUrl": "https://contoso.sharepoint.com/sites/Finance/Shared%20Documents/General/Tracker.xlsx",
	"cTag": '"c:{ABC},5"',
}


class TestHelpers(unittest.TestCase):
	def test_encode_share_url_matches_microsoft_example(self):
		url = "https://onedrive.live.com/redir?resid=1231244193912!12&authKey=1201919!12921!1"
		self.assertEqual(encode_share_url(url), "u!aHR0cHM6Ly9vbmVkcml2ZS5saXZlLmNvbS9yZWRpcj9yZXNpZD0xMjMxMjQ0MTkzOTEyITEyJmF1dGhLZXk9MTIwMTkxOSExMjkyMSEx")

	def test_parse_graph_time(self):
		self.assertEqual(parse_graph_time("2026-11-07T13:42:10Z"), dt.datetime(2026, 11, 7, 13, 42, 10))
		self.assertEqual(parse_graph_time("2026-11-07T13:42:10.1234567Z"), dt.datetime(2026, 11, 7, 13, 42, 10, 123456))
		self.assertIsNone(parse_graph_time(None))

	def test_editor_from_item(self):
		item = {"lastModifiedBy": {"user": {"displayName": "Ahmed Ali", "email": "ahmed@contoso.com", "id": "u1"}}}
		self.assertEqual(editor_from_item(item), {"name": "Ahmed Ali", "email": "ahmed@contoso.com", "id": "u1"})
		self.assertEqual(editor_from_item({})["name"], "Someone")

	def test_check_excel_item(self):
		check_excel_item(EXCEL_ITEM)
		with self.assertRaises(GraphError):
			check_excel_item({"name": "Notes.docx", "file": {}})
		with self.assertRaises(GraphError):
			check_excel_item({"name": "General", "folder": {}})


class TestClient(unittest.TestCase):
	def test_token_is_cached_and_roles_read(self):
		session = FakeSession([])
		c = client(session)
		token = c.get_token()
		self.assertEqual(c.get_token(), token)
		self.assertEqual(sum(1 for call in session.calls if call[0] == "POST"), 1)
		self.assertEqual(c.token_roles(token), ["Files.Read.All"])

	def test_bad_secret_message(self):
		session = FakeSession([], FakeResponse(401, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."}))
		with self.assertRaises(GraphError) as ctx:
			client(session).get_token()
		self.assertIn("client secret is wrong", str(ctx.exception))

	def test_resolve_via_shares_api(self):
		session = FakeSession([("GET", "/shares/u!", FakeResponse(200, EXCEL_ITEM))])
		item = client(session).resolve_link("https://contoso.sharepoint.com/:x:/s/Finance/EUabc?e=xyz")
		self.assertEqual((item["id"], item["parentReference"]["driveId"]), ("ITEM1", "b!DRIVE"))

	def test_resolve_falls_back_to_path(self):
		session = FakeSession([
			("GET", "/shares/u!", FakeResponse(400, {"error": {"code": "invalidRequest", "message": "bad"}})),
			("GET", "/sites/contoso.sharepoint.com:/sites/Finance", FakeResponse(200, {"id": "SITE1"})),
			("GET", "/sites/SITE1/drives", FakeResponse(200, {"value": [
				{"id": "b!OTHER", "webUrl": "https://contoso.sharepoint.com/sites/Finance/Other"},
				{"id": "b!DRIVE", "webUrl": "https://contoso.sharepoint.com/sites/Finance/Shared%20Documents"},
			]})),
			("GET", "/drives/b!DRIVE/root:/General/Tracker%202026.xlsx", FakeResponse(200, EXCEL_ITEM)),
		])
		item = client(session).resolve_link("https://contoso.sharepoint.com/sites/Finance/Shared%20Documents/General/Tracker%202026.xlsx?web=1")
		self.assertEqual(item["id"], "ITEM1")

	def test_existing_access_link_is_resolved_by_path_first(self):
		session = FakeSession([
			("GET", "/sites/contoso.sharepoint.com:/sites/Finance", FakeResponse(200, {"id": "SITE1"})),
			("GET", "/sites/SITE1/drives", FakeResponse(200, {"value": [
				{"id": "b!DRIVE", "webUrl": "https://contoso.sharepoint.com/sites/Finance/Shared%20Documents"},
			]})),
			("GET", "/drives/b!DRIVE/root:/General/Tracker.xlsx", FakeResponse(200, EXCEL_ITEM)),
		])
		link = "https://contoso.sharepoint.com/:x:/r/sites/Finance/Shared%20Documents/General/Tracker.xlsx?d=w1a2b&csf=1&web=1&e=Xy"
		self.assertEqual(client(session).resolve_link(link)["id"], "ITEM1")
		self.assertFalse(any("/shares/" in url for _, url in session.calls))

	def test_lookup_denied_names_the_missing_permission(self):
		denied = FakeResponse(403, {"error": {"code": "accessDenied", "message": "denied"}})
		session = FakeSession([("GET", "/shares/", denied), ("GET", "/sites/", denied)])
		with self.assertRaises(GraphError) as ctx:
			client(session).resolve_link("https://contoso.sharepoint.com/sites/HR/Shared%20Documents/Tracker.xlsx")
		self.assertIn("Sites.Read.All", str(ctx.exception))

	def test_not_found_and_permission_errors(self):
		session = FakeSession([("GET", "/drives/D/items/I", FakeResponse(403, {"error": {"code": "accessDenied", "message": "denied"}}))])
		with self.assertRaises(GraphError) as ctx:
			client(session).get_item("D", "I")
		self.assertIn("Files.Read.All", str(ctx.exception))
		with self.assertRaises(GraphError) as ctx:
			client(FakeSession([])).get_item("D", "missing")
		self.assertIn("not found", str(ctx.exception))

	def test_download_limit(self):
		session = FakeSession([("GET", "/content", FakeResponse(200, None, content=b"x" * 3000))])
		self.assertEqual(len(client(session).download("D", "I", max_bytes=5000)), 3000)
		with self.assertRaises(GraphError):
			client(session).download("D", "I", max_bytes=1000)


if __name__ == "__main__":
	unittest.main()
