"""Microsoft Graph calls made with the app's own identity (client credentials).

Only reads are made: an access token, a file's metadata, its content, and
resolving a pasted link to the file. The app needs the application permissions
Files.Read.All and Sites.Read.All (or Sites.Selected with read access to the
department sites).
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
from urllib.parse import quote, unquote, urlparse

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
ITEM_FIELDS = "id,name,cTag,eTag,size,webUrl,lastModifiedDateTime,lastModifiedBy,file,folder,parentReference"
SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm")
READ_ROLES = ("Files.Read.All", "Files.ReadWrite.All", "Sites.Read.All", "Sites.ReadWrite.All", "Sites.Selected", "Sites.FullControl.All")
TIMEOUT = 30

TOKEN_HINTS = {
	"AADSTS7000215": "The client secret is wrong. Copy the secret's Value (not its Secret ID) into Teams Notify Settings.",
	"AADSTS7000222": "The client secret has expired. Create a new secret in Entra ID and paste its Value into Teams Notify Settings.",
	"AADSTS700016": "No app with this Application (client) ID exists in this tenant. Check the client ID and tenant ID.",
	"AADSTS90002": "The tenant ID was not found. Copy the Directory (tenant) ID from the app's Overview page.",
	"AADSTS900023": "The tenant ID is not valid. Copy the Directory (tenant) ID from the app's Overview page.",
	"AADSTS90023": "The tenant ID is not valid. Copy the Directory (tenant) ID from the app's Overview page.",
}


class GraphError(Exception):
	def __init__(self, message: str, status: int | None = None, code: str | None = None):
		super().__init__(message)
		self.status = status
		self.code = code


def encode_share_url(url: str) -> str:
	"""Graph's encoding for /shares/{id}: 'u!' + unpadded base64url of the link."""
	b64 = base64.b64encode(url.strip().encode("utf-8")).decode("ascii")
	return "u!" + b64.rstrip("=").replace("/", "_").replace("+", "-")


def parse_graph_time(value: str | None) -> _dt.datetime | None:
	"""'2026-11-07T13:42:10.123Z' -> naive UTC datetime."""
	if not value:
		return None
	text = value.strip().replace("Z", "+00:00")
	if "." in text:
		head, _, tail = text.partition(".")
		digits = "".join(ch for ch in tail if ch.isdigit())
		offset = tail[len(digits):]
		text = f"{head}.{digits[:6].ljust(6, '0')}{offset}"
	parsed = _dt.datetime.fromisoformat(text)
	if parsed.tzinfo:
		parsed = parsed.astimezone(_dt.timezone.utc).replace(tzinfo=None)
	return parsed


def editor_from_item(item: dict) -> dict:
	"""Who saved the file last, as {"name", "email", "id"}."""
	modified_by = item.get("lastModifiedBy") or {}
	who = modified_by.get("user") or modified_by.get("application") or modified_by.get("device") or {}
	email = who.get("email") or who.get("userPrincipalName") or ""
	return {"name": who.get("displayName") or email or "Someone", "email": email, "id": who.get("id") or ""}


SITE_PREFIXES = ("sites", "teams", "personal")


def _path_segments(link: str):
	"""Path parts of a link that names the file directly, or None for short sharing links.

	Accepts ".../sites/HR/Shared Documents/General/file.xlsx" and the
	"People with existing access" form ".../:x:/r/sites/HR/Shared Documents/file.xlsx?d=...".
	"""
	path = unquote(urlparse(link).path)
	if "/_layouts/" in path.lower() or path.lower().endswith(".aspx"):
		return None
	segments = [s for s in path.split("/") if s]
	if segments and segments[0].startswith(":") and segments[0].endswith(":"):
		if len(segments) < 2 or segments[1] != "r":
			return None  # /:x:/s/HR/EAbc... is a sharing token, not a path
		segments = segments[2:]
	if not segments or "." not in segments[-1]:
		return None
	if segments[0].lower() in SITE_PREFIXES and len(segments) < 4:
		return None
	return segments


def check_excel_item(item: dict) -> None:
	name = item.get("name") or ""
	if item.get("folder") is not None or item.get("file") is None:
		raise GraphError(f"'{name}' is a folder, not a file. Copy the link of the Excel file itself.")
	if not name.lower().endswith(SUPPORTED_EXTENSIONS):
		raise GraphError(f"'{name}' is not an .xlsx or .xlsm workbook, so its cells can't be compared.")


class _FrappeCache:
	def get(self, key):
		import frappe

		return frappe.cache().get_value(key)

	def set(self, key, value, ttl):
		import frappe

		frappe.cache().set_value(key, value, expires_in_sec=ttl)


class _MemoryCache:
	def __init__(self):
		self.data = {}

	def get(self, key):
		return self.data.get(key)

	def set(self, key, value, ttl):
		self.data[key] = value


class GraphClient:
	def __init__(self, tenant_id: str, client_id: str, client_secret: str, cache=None, session=None):
		self.tenant_id = (tenant_id or "").strip()
		self.client_id = (client_id or "").strip()
		self.client_secret = client_secret or ""
		self.cache = cache if cache is not None else _FrappeCache()
		self.http = session or requests.Session()
		fingerprint = hashlib.sha256(f"{self.tenant_id}|{self.client_id}|{self.client_secret}".encode()).hexdigest()[:16]
		self.cache_key = f"teams_excel_notify:graph_token:{fingerprint}"

	# ------------------------------------------------------------------ token

	def get_token(self, force: bool = False) -> str:
		if not (self.tenant_id and self.client_id and self.client_secret):
			raise GraphError("Teams Notify Settings is missing the tenant ID, client ID or client secret.")
		if not force:
			cached = self.cache.get(self.cache_key)
			if cached:
				return cached
		try:
			response = self.http.post(
				f"{LOGIN}/{quote(self.tenant_id)}/oauth2/v2.0/token",
				data={
					"client_id": self.client_id,
					"client_secret": self.client_secret,
					"scope": "https://graph.microsoft.com/.default",
					"grant_type": "client_credentials",
				},
				timeout=TIMEOUT,
			)
		except requests.RequestException as exc:
			raise GraphError(f"Could not reach Microsoft sign-in: {exc}") from exc
		try:
			data = response.json()
		except ValueError:
			data = {}
		if response.status_code != 200 or "access_token" not in data:
			description = data.get("error_description") or response.text[:300]
			code = next((c for c in TOKEN_HINTS if c in description), None)
			hint = TOKEN_HINTS.get(code, "Microsoft refused to issue a token.")
			raise GraphError(f"{hint} ({description.splitlines()[0] if description else response.status_code})", response.status_code, code)
		token = data["access_token"]
		ttl = max(60, int(data.get("expires_in", 3600)) - 300)
		self.cache.set(self.cache_key, token, ttl)
		return token

	def token_roles(self, token: str | None = None) -> list:
		"""Application permissions inside the token (shows whether admin consent was granted)."""
		token = token or self.get_token()
		try:
			payload = token.split(".")[1]
			payload += "=" * (-len(payload) % 4)
			claims = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
		except Exception:
			return []
		return sorted(claims.get("roles") or [])

	# ------------------------------------------------------------------ requests

	def _request(self, method: str, url: str, *, params=None, stream=False, allow_retry=True):
		token = self.get_token()
		try:
			response = self.http.request(
				method,
				url,
				params=params,
				headers={"Authorization": f"Bearer {token}"},
				timeout=TIMEOUT if not stream else 120,
				stream=stream,
			)
		except requests.RequestException as exc:
			raise GraphError(f"Could not reach Microsoft Graph: {exc}") from exc
		if response.status_code == 401 and allow_retry:
			self.get_token(force=True)
			return self._request(method, url, params=params, stream=stream, allow_retry=False)
		if response.status_code >= 400:
			raise self._error(response)
		return response

	@staticmethod
	def _error(response) -> GraphError:
		code, message = None, response.text[:300]
		try:
			err = response.json().get("error") or {}
			code, message = err.get("code"), err.get("message") or message
		except ValueError:
			pass
		status = response.status_code
		if status == 404 or code == "itemNotFound":
			text = "The file was not found. It may have been deleted or moved to another Team or site. Paste its link again and save."
		elif status == 403 or code in ("accessDenied", "Authorization_RequestDenied"):
			text = (
				"The app is not allowed to read this file. In Entra ID, check that the application permissions "
				"Files.Read.All and Sites.Read.All are added and admin consent is granted "
				"(with Sites.Selected, grant the app read access to this site)."
			)
		elif status == 429 or status >= 500:
			text = "Microsoft Graph is busy or throttling requests. The app will try again on the next check."
		else:
			text = "Microsoft Graph returned an error."
		return GraphError(f"{text} [{status} {code or ''}: {message}]".replace(" :", ":"), status, code)

	def _get_json(self, url: str, params=None) -> dict:
		return self._request("GET", url, params=params).json()

	# ------------------------------------------------------------------ files

	def get_item(self, drive_id: str, item_id: str) -> dict:
		return self._get_json(f"{GRAPH}/drives/{quote(drive_id)}/items/{quote(item_id)}", params={"$select": ITEM_FIELDS})

	def download(self, drive_id: str, item_id: str, max_bytes: int) -> bytes:
		response = self._request("GET", f"{GRAPH}/drives/{quote(drive_id)}/items/{quote(item_id)}/content", stream=True)
		chunks, size = [], 0
		for chunk in response.iter_content(chunk_size=256 * 1024):
			size += len(chunk)
			if size > max_bytes:
				response.close()
				raise GraphError(f"The file is larger than {max_bytes // (1024 * 1024)} MB, the limit set in Teams Notify Settings.")
			chunks.append(chunk)
		return b"".join(chunks)

	def resolve_link(self, link: str) -> dict:
		"""Find the drive item behind a link copied from Teams, SharePoint or OneDrive.

		Links that contain the file's path (".../sites/HR/Shared Documents/file.xlsx", or a
		"People with existing access" link ".../:x:/r/sites/...") are looked up by path, which
		needs only read permissions (Sites.Read.All). Short sharing links (".../:x:/s/HR/EAbc...")
		go through the shares API, which Microsoft documents as needing Files.ReadWrite.All.
		"""
		link = (link or "").strip()
		if not link.lower().startswith("https://"):
			raise GraphError("Paste the full link to the file, starting with https://")
		self.get_token()  # credential problems should surface as themselves
		attempts = [self._resolve_by_path, self._resolve_by_share]
		if _path_segments(link) is None:
			attempts.reverse()
		errors = []
		for attempt in attempts:
			try:
				item = attempt(link)
				break
			except GraphError as exc:
				errors.append(exc)
		else:
			if all(e.status == 403 for e in errors if e.status):
				raise GraphError(
					"The app is not allowed to look up this file. Add the application permission Sites.Read.All "
					"(next to Files.Read.All) and grant admin consent, or paste a link that contains the file's path "
					"(see below). [" + "; ".join(str(e) for e in errors) + "]",
					403,
				)
			raise GraphError(
				"Could not find the file from this link. In Teams open the Files tab, click ⋯ next to the file, "
				"choose Copy link, change the link setting to 'People with existing access', copy and paste it here. "
				"Or open the file's Details in SharePoint, copy its Path and paste that. ["
				+ "; ".join(str(e) for e in errors)
				+ "]",
				errors[0].status,
				errors[0].code,
			)
		check_excel_item(item)
		return item

	def _resolve_by_share(self, link: str) -> dict:
		return self._get_json(f"{GRAPH}/shares/{encode_share_url(link)}/driveItem", params={"$select": ITEM_FIELDS})

	def _resolve_by_path(self, link: str) -> dict:
		host = urlparse(link).netloc
		segments = _path_segments(link)
		if segments is None:
			raise GraphError("The link doesn't contain the file's path.")
		if segments[0].lower() in SITE_PREFIXES:
			site_url = f"{GRAPH}/sites/{host}:/{quote('/'.join(segments[:2]))}"
		else:
			site_url = f"{GRAPH}/sites/{host}"  # the tenant's root site
		site = self._get_json(site_url, params={"$select": "id,webUrl"})
		drives = self._get_json(f"{GRAPH}/sites/{site['id']}/drives", params={"$select": "id,name,webUrl"}).get("value", [])
		file_path = "/" + "/".join(segments)
		best = None
		for drive in drives:
			drive_path = unquote(urlparse(drive.get("webUrl") or "").path).rstrip("/")
			if drive_path and file_path.lower().startswith(drive_path.lower() + "/"):
				if best is None or len(drive_path) > len(best[1]):
					best = (drive, drive_path)
		if best is None:
			raise GraphError("No document library on this site matches the link.")
		relative = file_path[len(best[1]) + 1 :]
		return self._get_json(f"{GRAPH}/drives/{best[0]['id']}/root:/{quote(relative)}", params={"$select": ITEM_FIELDS})
