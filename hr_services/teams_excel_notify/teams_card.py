"""Build Adaptive Cards for a Teams channel and post them to a Workflows webhook.

No Frappe imports, so it can be unit tested on its own.
"""

from __future__ import annotations

import json

import requests

CARD_VERSION = "1.4"
MAX_CARD_BYTES = 24_000  # Teams rejects messages over ~28 KB
MAX_VALUE_CHARS = 120
MAX_ROW_VALUES = 8

LABELS = {
	"en": {
		"title": "{file} was updated",
		"title_part": "{file} was updated ({part}/{parts})",
		"edited_by": "Edited by",
		"when": "When",
		"changes": "Changes",
		"sheet": "Sheet",
		"row": "Row {n}",
		"was_row": "was row {n}",
		"row_added": "Row {n} added",
		"row_removed": "Row {n} removed",
		"row_moved": "moved from row {n}",
		"rows_moved": "{n} rows were sorted or moved",
		"col_added": "Column added: {name}",
		"col_removed": "Column removed: {name}",
		"col_renamed": "Column renamed: {old} → {new}",
		"sheet_added": "Sheet added ({n} rows)",
		"sheet_removed": "Sheet removed",
		"sheet_renamed": "Sheet renamed from {old}",
		"empty": "(empty)",
		"arrow": " → ",
		"more": "…and {n} more changes. Open the file to see everything.",
		"more_one": "…and 1 more change. Open the file to see everything.",
		"open": "Open file",
		"count_cells": "{n} cells changed",
		"count_added": "{n} rows added",
		"count_removed": "{n} rows removed",
		"count_moved": "{n} rows moved",
		"count_other": "{n} other changes",
		"count_cells_one": "1 cell changed",
		"count_added_one": "1 row added",
		"count_removed_one": "1 row removed",
		"count_moved_one": "1 row moved",
		"count_other_one": "1 other change",
		"test_title": "Test message for {file}",
		"test_body": "This channel will get a message here whenever someone changes {file}.",
	},
	"ar": {
		"title": "تم تحديث {file}",
		"title_part": "تم تحديث {file} ({part}/{parts})",
		"edited_by": "تم التعديل بواسطة",
		"when": "الوقت",
		"changes": "التغييرات",
		"sheet": "الورقة",
		"row": "الصف {n}",
		"was_row": "كان الصف {n}",
		"row_added": "تمت إضافة الصف {n}",
		"row_removed": "تم حذف الصف {n}",
		"row_moved": "نُقل من الصف {n}",
		"rows_moved": "تم فرز أو نقل {n} صفوف",
		"col_added": "تمت إضافة عمود: {name}",
		"col_removed": "تم حذف عمود: {name}",
		"col_renamed": "تمت إعادة تسمية العمود: {old} ← {new}",
		"sheet_added": "تمت إضافة ورقة ({n} صفوف)",
		"sheet_removed": "تم حذف الورقة",
		"sheet_renamed": "تمت إعادة تسمية الورقة من {old}",
		"empty": "(فارغ)",
		"arrow": " ← ",
		"more": "…و{n} تغييرات أخرى. افتح الملف لرؤية كل التغييرات.",
		"open": "فتح الملف",
		"count_cells": "{n} خلايا معدلة",
		"count_added": "{n} صفوف مضافة",
		"count_removed": "{n} صفوف محذوفة",
		"count_moved": "{n} صفوف منقولة",
		"count_other": "{n} تغييرات أخرى",
		"test_title": "رسالة تجريبية لملف {file}",
		"test_body": "ستصل رسالة إلى هذه القناة كلما عدّل أحد الملف {file}.",
	},
}


class TeamsPostError(Exception):
	pass


def _t(lang: str, key: str, **kw) -> str:
	table = LABELS.get(lang, LABELS["en"])
	if kw.get("n") == 1 and f"{key}_one" in table:
		key = f"{key}_one"
	text = table.get(key) or LABELS["en"][key]
	return text.format(**kw)


def _short(value: str) -> str:
	value = " ".join(str(value).split())
	return value if len(value) <= MAX_VALUE_CHARS else value[: MAX_VALUE_CHARS - 1] + "…"


def _run(text: str, **style) -> dict:
	run = {"type": "TextRun", "text": text}
	run.update(style)
	return run


def _value_runs(lang: str, value: str, old: bool) -> list:
	if not value:
		return [_run(_t(lang, "empty"), italic=True, isSubtle=True)]
	if old:
		return [_run(_short(value), isSubtle=True, strikethrough=True)]
	return [_run(_short(value), weight="Bolder")]


def _line(inlines: list, align: str | None) -> dict:
	block = {"type": "RichTextBlock", "inlines": inlines, "spacing": "Small"}
	if align:
		block["horizontalAlignment"] = align
	return block


def item_block(item: dict, lang: str = "en", align: str | None = None) -> dict:
	"""One RichTextBlock describing one change."""
	kind = item["kind"]
	label = item.get("label") or ""
	runs = []
	if kind == "cell":
		where = _t(lang, "row", n=item["row"])
		if label:
			where += f" · {_short(label)}"
		runs.append(_run(where, weight="Bolder"))
		if item.get("moved") or item.get("old_row", item["row"]) != item["row"]:
			runs.append(_run(f" ({_t(lang, 'was_row', n=item['old_row'])})", isSubtle=True))
		runs.append(_run(" — "))
		for i, cell in enumerate(item["cells"]):
			if i:
				runs.append(_run(";  "))
			runs.append(_run(f"{_short(cell['col'])}: "))
			runs += _value_runs(lang, cell["old"], old=True)
			runs.append(_run(_t(lang, "arrow")))
			runs += _value_runs(lang, cell["new"], old=False)
	elif kind in ("row_added", "row_removed"):
		key, color = ("row_added", "Good") if kind == "row_added" else ("row_removed", "Attention")
		runs.append(_run(_t(lang, key, n=item["row"]), weight="Bolder", color=color))
		if label:
			runs.append(_run(f" · {_short(label)}", weight="Bolder"))
		values = item.get("values") or []
		shown = ", ".join(f"{_short(k)}: {_short(v)}" for k, v in values[:MAX_ROW_VALUES])
		if len(values) > MAX_ROW_VALUES:
			shown += ", …"
		if shown:
			runs.append(_run(f" — {shown}"))
	elif kind == "row_moved":
		where = _t(lang, "row", n=item["row"])
		if label:
			where += f" · {_short(label)}"
		runs.append(_run(where, weight="Bolder"))
		runs.append(_run(" " + _t(lang, "row_moved", n=item["old_row"]), italic=True))
	elif kind == "rows_moved":
		runs.append(_run(_t(lang, "rows_moved", n=item["count"]), italic=True))
	elif kind == "col_added":
		runs.append(_run(_t(lang, "col_added", name=_short(item["name"])), weight="Bolder", color="Good"))
	elif kind == "col_removed":
		runs.append(_run(_t(lang, "col_removed", name=_short(item["name"])), weight="Bolder", color="Attention"))
	elif kind == "col_renamed":
		runs.append(_run(_t(lang, "col_renamed", old=_short(item["old"]), new=_short(item["new"])), weight="Bolder"))
	elif kind == "sheet_added":
		runs.append(_run(_t(lang, "sheet_added", n=item["rows"]), weight="Bolder", color="Good"))
	elif kind == "sheet_removed":
		runs.append(_run(_t(lang, "sheet_removed"), weight="Bolder", color="Attention"))
	elif kind == "sheet_renamed":
		runs.append(_run(_t(lang, "sheet_renamed", old=_short(item["old"])), weight="Bolder"))
	else:
		runs.append(_run(kind))
	return _line(runs, align)


def _summary(report: dict, lang: str) -> str:
	counts = report.get("counts", {})
	parts = []
	if counts.get("cells"):
		parts.append(_t(lang, "count_cells", n=counts["cells"]))
	if counts.get("row_added"):
		parts.append(_t(lang, "count_added", n=counts["row_added"]))
	if counts.get("row_removed"):
		parts.append(_t(lang, "count_removed", n=counts["row_removed"]))
	if counts.get("rows_moved"):
		parts.append(_t(lang, "count_moved", n=counts["rows_moved"]))
	other = sum(v for k, v in counts.items() if k not in ("cells", "rows_changed", "row_added", "row_removed", "rows_moved"))
	if other:
		parts.append(_t(lang, "count_other", n=other))
	return " · ".join(parts)


def _heading(text: str, align: str | None, size: str = "Default") -> dict:
	block = {"type": "TextBlock", "text": text, "weight": "Bolder", "wrap": True, "spacing": "Medium", "size": size, "separator": True}
	if align:
		block["horizontalAlignment"] = align
	return block


def _editor_name(report: dict, index) -> str:
	editors = report.get("editors") or []
	if index is None or index >= len(editors):
		return "Someone"
	return editors[index].get("name") or "Someone"


def _sections(report: dict, lang: str, align: str | None) -> list:
	"""(heading, [blocks]) groups: by sheet, or by person then sheet when several edited."""
	items = report["items"]
	sheets = list(dict.fromkeys(item["sheet"] for item in items))
	editors = list(dict.fromkeys(item.get("by") for item in items))
	sections = []
	if len(editors) > 1:
		for who in editors:
			mine = [i for i in items if i.get("by") == who]
			for sheet in dict.fromkeys(i["sheet"] for i in mine):
				heading = _editor_name(report, who)
				if len(sheets) > 1:
					heading += f" · {sheet}"
				sections.append((heading, [item_block(i, lang, align) for i in mine if i["sheet"] == sheet]))
	else:
		for sheet in sheets:
			heading = f"{_t(lang, 'sheet')}: {sheet}" if len(sheets) > 1 else None
			sections.append((heading, [item_block(i, lang, align) for i in items if i["sheet"] == sheet]))
	return sections


def _card(body: list, file_url: str | None, lang: str) -> dict:
	card = {
		"type": "AdaptiveCard",
		"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
		"version": CARD_VERSION,
		"msteams": {"width": "Full"},
		"body": body,
	}
	if file_url and file_url.startswith("https://"):
		card["actions"] = [{"type": "Action.OpenUrl", "title": _t(lang, "open"), "url": file_url}]
	return card


def _size(obj) -> int:
	return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def build_cards(
	report: dict,
	*,
	file_name: str,
	file_url: str | None,
	when_text: str,
	language: str = "en",
	max_items: int = 25,
	max_cards: int = 3,
) -> list:
	"""Cards for one notification. Long change lists are split over several cards."""
	lang = "ar" if str(language).lower().startswith("ar") else "en"
	align = "Right" if lang == "ar" else None
	max_items = max(1, int(max_items or 25))
	max_cards = max(1, int(max_cards or 3))

	names = [_editor_name(report, i) for i in dict.fromkeys(item.get("by") for item in report["items"])]
	facts = [
		{"title": _t(lang, "edited_by"), "value": ", ".join(names) or "Someone"},
		{"title": _t(lang, "when"), "value": when_text},
		{"title": _t(lang, "changes"), "value": _summary(report, lang) or str(report.get("total", 0))},
	]
	sheets = list(dict.fromkeys(item["sheet"] for item in report["items"]))
	if len(sheets) == 1:
		facts.append({"title": _t(lang, "sheet"), "value": sheets[0]})

	# Flatten into (heading, block) so a long section can continue on the next card.
	flat = []
	for heading, blocks in _sections(report, lang, align):
		for block in blocks:
			flat.append((heading, block))

	pages, page, page_bytes, last_heading = [], [], 0, object()
	for heading, block in flat:
		cost = _size(block) + (_size(_heading(heading, align)) if heading and heading != last_heading else 0)
		if page and (len([b for b in page if b["type"] == "RichTextBlock"]) >= max_items or page_bytes + cost > MAX_CARD_BYTES - 3000):
			pages.append(page)
			page, page_bytes, last_heading = [], 0, object()
			cost = _size(block) + (_size(_heading(heading, align)) if heading else 0)
		if heading and heading != last_heading:
			page.append(_heading(heading, align))
		page.append(block)
		page_bytes += cost
		last_heading = heading
	if page:
		pages.append(page)

	hidden = 0
	if len(pages) > max_cards:
		for extra in pages[max_cards:]:
			hidden += sum(1 for b in extra if b["type"] == "RichTextBlock")
		pages = pages[:max_cards]

	cards = []
	for n, blocks in enumerate(pages, start=1):
		if len(pages) > 1:
			title = _t(lang, "title_part", file=file_name, part=n, parts=len(pages))
		else:
			title = _t(lang, "title", file=file_name)
		body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]
		if align:
			body[0]["horizontalAlignment"] = align
		if n == 1:
			body.append({"type": "FactSet", "facts": facts})
		body += blocks
		if n == len(pages) and hidden:
			note = {"type": "TextBlock", "text": _t(lang, "more", n=hidden), "wrap": True, "isSubtle": True, "spacing": "Medium", "separator": True}
			if align:
				note["horizontalAlignment"] = align
			body.append(note)
		cards.append(_card(body, file_url, lang))
	return cards


def build_test_card(*, file_name: str, file_url: str | None, language: str = "en") -> dict:
	lang = "ar" if str(language).lower().startswith("ar") else "en"
	align = "Right" if lang == "ar" else None
	body = [
		{"type": "TextBlock", "text": _t(lang, "test_title", file=file_name), "weight": "Bolder", "size": "Medium", "wrap": True},
		{"type": "TextBlock", "text": _t(lang, "test_body", file=file_name), "wrap": True},
	]
	if align:
		for block in body:
			block["horizontalAlignment"] = align
	return _card(body, file_url, lang)


def post_card(webhook_url: str, card: dict, timeout: int = 30) -> str:
	"""Send one card to a Teams Workflows webhook. Returns the HTTP status line."""
	payload = {
		"type": "message",
		"attachments": [
			{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
		],
	}
	try:
		response = requests.post(webhook_url, json=payload, timeout=timeout)
	except requests.RequestException as exc:
		raise TeamsPostError(f"Could not reach the Teams workflow: {exc}") from exc
	if response.status_code >= 300:
		raise TeamsPostError(f"Teams workflow answered HTTP {response.status_code}: {response.text[:500]}")
	return f"HTTP {response.status_code}"
