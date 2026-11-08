"""Small helpers shared by the scheduler and the forms."""

from __future__ import annotations

import base64
import json
import zlib

_PREFIX = "z1:"


def pack(obj) -> str:
	"""JSON -> zlib -> base64, for storing snapshots in a Long Text field."""
	raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
	return _PREFIX + base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def unpack(text: str | None):
	if not text:
		return None
	if not text.startswith(_PREFIX):
		return json.loads(text)
	return json.loads(zlib.decompress(base64.b64decode(text[len(_PREFIX):])).decode("utf-8"))


def split_list(text: str | None) -> list:
	"""Values separated by new lines or commas."""
	parts = []
	for line in (text or "").replace("\r", "\n").split("\n"):
		for part in line.split(","):
			part = part.strip()
			if part:
				parts.append(part)
	return parts
