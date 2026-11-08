"""Change detection between spreadsheet snapshots.

This module has no Frappe imports, so it can be unit tested on its own.

Terms
-----
snapshot  {"v": 1, "order": [sheet names], "sheets": {name: {"rows": [[str, ...]], "hidden": bool}}}
          Row r (0-based) is Excel row r + 1; column c (0-based) is Excel column c + 1.
step      the difference between two consecutive snapshots taken while people edit.
session   every step between the last notification (the baseline) and now.

How it works
------------
Each step aligns the previous snapshot with the new one: columns by header name,
rows by content (so inserted, deleted and sorted rows are recognised). The step
records who changed which cell and keeps a lineage from every current row and
column back to the baseline. When the session ends, ``Session.finalize`` compares
the final snapshot with the baseline through that lineage, so each reported change
carries the name of the person who made it, even when several people edited.
"""

from __future__ import annotations

import difflib
from bisect import bisect_left
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

SNAPSHOT_VERSION = 1

# Two rows inside the same edited block are treated as "the same row, edited"
# when at least this share of their non-empty cells is equal.
PAIR_IN_BLOCK = 0.5
# Stricter threshold for rows that changed AND moved somewhere else.
PAIR_MOVED = 0.6
# Extra similarity when the label column (e.g. the employee name) is equal.
LABEL_BONUS = 0.3
# Upper bound on row-to-row comparisons when pairing moved rows.
GLOBAL_PAIR_LIMIT = 250_000
# Up to this many moved rows are listed one by one; more becomes "N rows were moved".
LIST_MOVED_ROWS = 3


# --------------------------------------------------------------------------- helpers


def _norm(text) -> str:
	return " ".join(str(text or "").split()).casefold()


def column_letter(index: int) -> str:
	"""0 -> A, 25 -> Z, 26 -> AA."""
	n, letters = index + 1, ""
	while n:
		n, rem = divmod(n - 1, 26)
		letters = chr(65 + rem) + letters
	return letters


def column_index(letters: str) -> int:
	"""A -> 0, Z -> 25, AA -> 26. Raises ValueError for anything else."""
	letters = (letters or "").strip().upper()
	if not letters or not letters.isascii() or not letters.isalpha():
		raise ValueError(f"Not a column letter: {letters!r}")
	n = 0
	for ch in letters:
		n = n * 26 + (ord(ch) - 64)
	return n - 1


def _cell(rows: list, r, c) -> str:
	if r is None or c is None or r < 0 or c < 0 or r >= len(rows):
		return ""
	row = rows[r]
	return row[c] if c < len(row) else ""


def _width(rows: list) -> int:
	return max((len(row) for row in rows), default=0)


def _has_content(rows: list, r: int, cols) -> bool:
	return any(_cell(rows, r, c) for c in cols)


def similarity(a: tuple, b: tuple) -> float:
	"""Share of non-empty positions holding the same value (1.0 for two blank rows)."""
	total = same = 0
	for x, y in zip(a, b):
		if x or y:
			total += 1
			if x == y:
				same += 1
	return 1.0 if total == 0 else same / total


def lis_positions(seq: list) -> set:
	"""Positions (indexes into seq) of one longest strictly increasing subsequence."""
	if not seq:
		return set()
	tails, tails_pos, prev = [], [], [-1] * len(seq)
	for i, value in enumerate(seq):
		k = bisect_left(tails, value)
		if k == len(tails):
			tails.append(value)
			tails_pos.append(i)
		else:
			tails[k] = value
			tails_pos[k] = i
		prev[i] = tails_pos[k - 1] if k else -1
	keep, i = set(), tails_pos[-1]
	while i != -1:
		keep.add(i)
		i = prev[i]
	return keep


@dataclass(frozen=True)
class DiffConfig:
	header_row: int = 1  # Excel row with the column names; 0 = the sheet has no header row
	label_columns: tuple = (0,)  # 0-based columns whose values name a row (e.g. employee name)
	ignore_columns: frozenset = frozenset()  # normalised header names that are never compared

	@property
	def header_index(self):
		return self.header_row - 1 if self.header_row and self.header_row > 0 else None

	def ignored(self, header) -> bool:
		return bool(header) and _norm(header) in self.ignore_columns

	@classmethod
	def build(cls, header_row=1, label_columns="A", ignore_columns=""):
		def parts(text):
			text = str(text or "").replace("\r", "\n").replace(";", ",").replace("\n", ",")
			return [p.strip() for p in text.split(",") if p.strip()]

		labels = tuple(column_index(p) for p in parts(label_columns))
		ignore = frozenset(_norm(p) for p in parts(ignore_columns))
		return cls(header_row=max(0, int(header_row or 0)), label_columns=labels, ignore_columns=ignore)


def _headers(rows: list, cfg: DiffConfig, width: int) -> list:
	h = cfg.header_index
	if h is None:
		return [""] * width
	return [_cell(rows, h, c) for c in range(width)]


def _label(rows: list, r: int, cols) -> str:
	return " · ".join(v for v in (_cell(rows, r, c) for c in cols) if v)


# --------------------------------------------------------------------------- alignment


def _column_values(rows: list, c: int, header_index) -> Counter:
	return Counter(_cell(rows, r, c) for r in range(len(rows)) if r != header_index and _cell(rows, r, c))


def _column_overlap(a: Counter, b: Counter) -> float:
	if not a or not b:
		return 0.0
	return sum((a & b).values()) / max(sum(a.values()), sum(b.values()))


def map_columns(prev_headers: list, new_headers: list, prev_rows=None, new_rows=None, header_index=None) -> list:
	"""Match the columns of two header rows of equal length.

	Columns are matched by (header name, occurrence), so inserting, deleting or
	moving a column keeps the others matched. A column whose header changed is
	matched to the unmatched old column holding the same values (when rows are
	given), otherwise to the old column in the same position. Returns
	``new column -> previous column`` (None for a column that is new).
	"""
	width = len(new_headers)
	if len(prev_headers) != width:
		raise ValueError("header rows must have the same length")
	if prev_headers == new_headers:
		return list(range(width))

	def keys(headers):
		seen, out = defaultdict(int), []
		for header in headers:
			n = _norm(header)
			out.append((n, seen[n]))
			seen[n] += 1
		return out

	prev_pos = {key: i for i, key in enumerate(keys(prev_headers))}
	col_map, used = [None] * width, set()
	for j, key in enumerate(keys(new_headers)):
		i = prev_pos.get(key)
		if i is not None:
			col_map[j] = i
			used.add(i)

	def same_kind(i, j):  # both named or both blank
		return bool(_norm(new_headers[j])) == bool(_norm(prev_headers[i]))

	# Renamed columns: pair by the values they hold.
	if prev_rows is not None and new_rows is not None:
		free_new = [j for j in range(width) if col_map[j] is None]
		free_prev = [i for i in range(width) if i not in used]
		if free_new and free_prev and len(free_new) * len(free_prev) <= 400:
			new_vals = {j: _column_values(new_rows, j, header_index) for j in free_new}
			prev_vals = {i: _column_values(prev_rows, i, header_index) for i in free_prev}
			candidates = []
			for j in free_new:
				for i in free_prev:
					if same_kind(i, j):
						score = _column_overlap(prev_vals[i], new_vals[j])
						if score >= 0.5:
							candidates.append((-score, abs(i - j), j, i))
			candidates.sort()
			for _, _, j, i in candidates:
				if col_map[j] is None and i not in used:
					col_map[j] = i
					used.add(i)

	# Otherwise a header edited in place is a rename.
	for j in range(width):
		if col_map[j] is None and j not in used and same_kind(j, j):
			col_map[j] = j
			used.add(j)
	return col_map


def align_rows(prev: list, new: list, prev_labels=None, new_labels=None) -> list:
	"""Match data rows of two sheets. ``prev``/``new`` hold equal-width tuples.

	Returns ``new index -> previous index`` (None for a row that is new).
	Unchanged rows are matched first (so inserts and deletes are exact), then
	moved rows with identical content, then edited rows by similarity.
	"""
	row_map = [None] * len(new)
	blocks = []
	matcher = difflib.SequenceMatcher(None, prev, new, autojunk=False)
	for tag, i1, i2, j1, j2 in matcher.get_opcodes():
		if tag == "equal":
			for k in range(i2 - i1):
				row_map[j1 + k] = i1 + k
		else:
			blocks.append((i1, i2, j1, j2))
	if not blocks:
		return row_map

	un_prev = [i for i1, i2, _, _ in blocks for i in range(i1, i2)]
	un_new = [j for _, _, j1, j2 in blocks for j in range(j1, j2)]
	used_prev = set()

	def blank(values):
		return not any(values)

	def score(i, j):
		s = similarity(prev[i], new[j])
		if prev_labels and new_labels and prev_labels[i] and prev_labels[i] == new_labels[j]:
			s += LABEL_BONUS
		return s

	# 1) Rows that moved without being edited: identical content elsewhere.
	by_content = defaultdict(deque)
	for i in un_prev:
		if not blank(prev[i]):
			by_content[prev[i]].append(i)
	for j in un_new:
		queue = by_content.get(new[j])
		if queue:
			i = queue.popleft()
			row_map[j] = i
			used_prev.add(i)

	# 2) Rows edited in place: pair inside each changed block, keeping order.
	for i1, i2, j1, j2 in blocks:
		ps = [i for i in range(i1, i2) if i not in used_prev]
		cs = [j for j in range(j1, j2) if row_map[j] is None]
		if not ps or not cs:
			continue
		if len(ps) == len(cs):
			for i, j in zip(ps, cs):
				if blank(prev[i]) == blank(new[j]):
					row_map[j] = i
					used_prev.add(i)
			continue
		start = 0
		for i in ps:
			if blank(prev[i]):
				continue
			best, best_k = 0.0, None
			for k in range(start, len(cs)):
				if blank(new[cs[k]]):
					continue
				s = score(i, cs[k])
				if s > best:
					best, best_k = s, k
			if best_k is not None and best >= PAIR_IN_BLOCK:
				row_map[cs[best_k]] = i
				used_prev.add(i)
				start = best_k + 1

	# 3) Rows that were edited and moved: best remaining pairs anywhere.
	ps = [i for i in un_prev if i not in used_prev and not blank(prev[i])]
	cs = [j for j in un_new if row_map[j] is None and not blank(new[j])]
	if ps and cs and len(ps) * len(cs) <= GLOBAL_PAIR_LIMIT:
		candidates = []
		for i in ps:
			for j in cs:
				s = score(i, j)
				if s >= PAIR_MOVED:
					candidates.append((-s, i, j))
		candidates.sort()
		taken_i, taken_j = set(), set()
		for _, i, j in candidates:
			if i in taken_i or j in taken_j:
				continue
			row_map[j] = i
			taken_i.add(i)
			taken_j.add(j)
	return row_map


@dataclass
class SheetAlignment:
	width: int
	col_map: list  # new column -> previous column or None
	row_map: list  # new row -> previous row or None (header row maps to itself)
	compare_cols: list  # new columns compared cell by cell
	prev_headers: list
	new_headers: list


def align_sheet(prev_rows: list, new_rows: list, cfg: DiffConfig, min_width: int = 0) -> SheetAlignment:
	width = max(_width(prev_rows), _width(new_rows), min_width)
	prev_h = _headers(prev_rows, cfg, width)
	new_h = _headers(new_rows, cfg, width)
	col_map = map_columns(prev_h, new_h, prev_rows, new_rows, cfg.header_index)
	compare = [
		c
		for c in range(width)
		if col_map[c] is not None and not cfg.ignored(new_h[c]) and not cfg.ignored(prev_h[col_map[c]])
	]
	h = cfg.header_index
	prev_idx = [r for r in range(len(prev_rows)) if r != h]
	new_idx = [r for r in range(len(new_rows)) if r != h]
	prev_proj = [tuple(_cell(prev_rows, r, col_map[c]) for c in compare) for r in prev_idx]
	new_proj = [tuple(_cell(new_rows, r, c) for c in compare) for r in new_idx]

	prev_label_cols = [
		col_map[c] if c < width and col_map[c] is not None else c for c in cfg.label_columns
	]
	prev_labels = [_label(prev_rows, r, prev_label_cols) for r in prev_idx]
	new_labels = [_label(new_rows, r, cfg.label_columns) for r in new_idx]

	data_map = align_rows(prev_proj, new_proj, prev_labels, new_labels)
	row_map = [None] * len(new_rows)
	if h is not None and h < len(new_rows) and h < len(prev_rows):
		row_map[h] = h
	for k, r in enumerate(new_idx):
		p = data_map[k]
		if p is not None:
			row_map[r] = prev_idx[p]
	return SheetAlignment(width, col_map, row_map, compare, prev_h, new_h)


# --------------------------------------------------------------------------- lineage


def _int_keys(d: dict) -> dict:
	return {int(k): v for k, v in d.items()}


@dataclass
class SheetLineage:
	"""How the current version of one sheet relates to the baseline."""

	base: str | None  # baseline sheet name, None when the sheet was added in this session
	base_width: int
	row_anc: list  # current row -> baseline row (or None)
	col_anc: list  # current column -> baseline column (or None); >= base_width = empty column
	cell_by: dict = field(default_factory=dict)  # (row, col) -> editor who changed it last
	row_added_by: dict = field(default_factory=dict)  # current row -> editor (rows with no ancestor)
	row_removed_by: dict = field(default_factory=dict)  # baseline row -> editor
	col_added_by: dict = field(default_factory=dict)  # current column -> editor
	col_removed_by: dict = field(default_factory=dict)  # baseline column -> editor
	moved_by: list = field(default_factory=list)  # editors who reordered rows
	renamed_by: int | None = None
	added_by: int | None = None

	@classmethod
	def from_baseline(cls, name: str, rows: list) -> SheetLineage:
		width = _width(rows)
		return cls(name, width, list(range(len(rows))), list(range(width)))

	@classmethod
	def new_sheet(cls, rows: list, editor: int) -> SheetLineage:
		return cls(None, 0, [None] * len(rows), [None] * _width(rows), added_by=editor)

	def to_dict(self) -> dict:
		return {
			"base": self.base,
			"base_width": self.base_width,
			"row_anc": self.row_anc,
			"col_anc": self.col_anc,
			"cell_by": [[r, c, e] for (r, c), e in self.cell_by.items()],
			"row_added_by": self.row_added_by,
			"row_removed_by": self.row_removed_by,
			"col_added_by": self.col_added_by,
			"col_removed_by": self.col_removed_by,
			"moved_by": self.moved_by,
			"renamed_by": self.renamed_by,
			"added_by": self.added_by,
		}

	@classmethod
	def from_dict(cls, d: dict) -> SheetLineage:
		return cls(
			base=d["base"],
			base_width=d["base_width"],
			row_anc=d["row_anc"],
			col_anc=d["col_anc"],
			cell_by={(r, c): e for r, c, e in d.get("cell_by", [])},
			row_added_by=_int_keys(d.get("row_added_by", {})),
			row_removed_by=_int_keys(d.get("row_removed_by", {})),
			col_added_by=_int_keys(d.get("col_added_by", {})),
			col_removed_by=_int_keys(d.get("col_removed_by", {})),
			moved_by=list(d.get("moved_by", [])),
			renamed_by=d.get("renamed_by"),
			added_by=d.get("added_by"),
		)


def _extend_col_anc(lin: SheetLineage, width: int) -> list:
	"""Previous column ancestry padded to ``width`` with (virtual) empty columns."""
	col_anc = list(lin.col_anc)
	if len(col_anc) >= width:
		return col_anc
	if lin.base is None:
		return col_anc + [None] * (width - len(col_anc))
	nxt = max([lin.base_width] + [a + 1 for a in col_anc if a is not None])
	while len(col_anc) < width:
		col_anc.append(nxt)
		nxt += 1
	return col_anc


def _step_sheet(lin: SheetLineage, prev_rows: list, new_rows: list, cfg: DiffConfig, editor: int) -> bool:
	"""Advance one sheet's lineage by one step. Returns True if any value changed."""
	al = align_sheet(prev_rows, new_rows, cfg, min_width=len(lin.col_anc))
	width, h = al.width, cfg.header_index
	col_anc = _extend_col_anc(lin, width)
	changed = False

	inv_col = {p: c for c, p in enumerate(al.col_map) if p is not None}
	inv_row = {p: r for r, p in enumerate(al.row_map) if p is not None}
	content_new = [c for c in range(width) if not cfg.ignored(al.new_headers[c])]
	content_prev = [c for c in range(width) if not cfg.ignored(al.prev_headers[c])]

	# Columns
	new_col_anc = [col_anc[p] if p is not None else None for p in al.col_map]
	col_added_by = {inv_col[p]: e for p, e in lin.col_added_by.items() if p in inv_col}
	for c, p in enumerate(al.col_map):
		if p is None and al.new_headers[c] and not cfg.ignored(al.new_headers[c]):
			col_added_by[c] = editor
			changed = True
	for p in range(width):
		if p in inv_col or cfg.ignored(al.prev_headers[p]):
			continue
		had_data = bool(al.prev_headers[p]) or any(_cell(prev_rows, r, p) for r in range(len(prev_rows)))
		if had_data:
			changed = True
			b = col_anc[p]
			if b is not None and b < lin.base_width:
				lin.col_removed_by[b] = editor

	# Rows
	new_row_anc = [lin.row_anc[p] if p is not None else None for p in al.row_map]
	cell_by = {}
	for (pr, pc), e in lin.cell_by.items():
		r, c = inv_row.get(pr), inv_col.get(pc)
		if r is not None and c is not None:
			cell_by[(r, c)] = e
	row_added_by = {inv_row[p]: e for p, e in lin.row_added_by.items() if p in inv_row}

	for r, p in enumerate(al.row_map):
		if r == h:
			if p is not None:
				for c in range(width):
					q = al.col_map[c]
					if q is not None and _norm(al.new_headers[c]) != _norm(al.prev_headers[q]):
						cell_by[(h, c)] = editor
						changed = True
			continue
		if p is None:
			if _has_content(new_rows, r, content_new):
				row_added_by[r] = editor
				changed = True
			continue
		for c in al.compare_cols:
			if _cell(new_rows, r, c) != _cell(prev_rows, p, al.col_map[c]):
				cell_by[(r, c)] = editor
				changed = True

	for p in range(len(prev_rows)):
		if p == h or p in inv_row:
			continue
		if _has_content(prev_rows, p, content_prev):
			changed = True
			b = lin.row_anc[p] if p < len(lin.row_anc) else None
			if b is not None:
				lin.row_removed_by[b] = editor

	seq = [p for r, p in enumerate(al.row_map) if p is not None and r != h]
	if any(b < a for a, b in zip(seq, seq[1:])):
		changed = True
		if editor not in lin.moved_by:
			lin.moved_by.append(editor)

	lin.row_anc, lin.col_anc = new_row_anc, new_col_anc
	lin.cell_by, lin.row_added_by, lin.col_added_by = cell_by, row_added_by, col_added_by
	return changed


def _sheet_rows(snapshot: dict, name: str) -> list:
	return snapshot["sheets"][name]["rows"]


def _match_renamed_sheets(gone: list, appeared: list, prev: dict, new: dict) -> dict:
	"""Pair sheets that disappeared with sheets that appeared when most rows are shared."""
	pairs, taken = {}, set()
	for new_name in appeared:
		new_rows = {tuple(row) for row in _sheet_rows(new, new_name) if any(row)}
		if not new_rows:
			continue
		best, best_name = 0.0, None
		for old_name in gone:
			if old_name in taken:
				continue
			old_rows = {tuple(row) for row in _sheet_rows(prev, old_name) if any(row)}
			if not old_rows:
				continue
			overlap = len(new_rows & old_rows) / max(len(new_rows), len(old_rows))
			if overlap > best:
				best, best_name = overlap, old_name
		if best_name is not None and best >= 0.6:
			pairs[new_name] = best_name
			taken.add(best_name)
	return pairs


def _editor_key(editor: dict) -> str:
	return (editor.get("id") or editor.get("email") or editor.get("name") or "").strip().lower()


# --------------------------------------------------------------------------- session


class Session:
	"""Everything that changed since the baseline, step by step."""

	def __init__(self, cfg: DiffConfig, cur: dict, lineage: dict, editors=None, removed_sheets=None, last_editor=None, steps=0, changed_steps=0, meta=None):
		self.cfg = cfg
		self.cur = cur
		self.lineage = lineage
		self.editors = editors or []
		self.removed_sheets = removed_sheets or {}  # baseline sheet name -> editor
		self.last_editor = last_editor
		self.steps = steps
		self.changed_steps = changed_steps
		self.meta = meta or {}

	@classmethod
	def start(cls, baseline: dict, cfg: DiffConfig) -> Session:
		lineage = {name: SheetLineage.from_baseline(name, _sheet_rows(baseline, name)) for name in baseline["order"]}
		return cls(cfg, baseline, lineage)

	@property
	def has_changes(self) -> bool:
		return self.changed_steps > 0

	def editor_index(self, editor: dict) -> int:
		key = _editor_key(editor)
		for i, known in enumerate(self.editors):
			if _editor_key(known) == key:
				return i
		self.editors.append({"name": editor.get("name") or "Someone", "email": editor.get("email") or "", "id": editor.get("id") or ""})
		return len(self.editors) - 1

	def step(self, new: dict, editor: dict) -> bool:
		"""Apply the next snapshot. Returns True if any value changed in this step."""
		e = self.editor_index(editor)
		prev = self.cur
		prev_names, new_names = prev["order"], new["order"]
		gone = [n for n in prev_names if n not in new["sheets"]]
		appeared = [n for n in new_names if n not in prev["sheets"]]
		renamed = _match_renamed_sheets(gone, appeared, prev, new)
		changed = False
		lineage = {}
		for name in new_names:
			old_name = name if name in prev["sheets"] else renamed.get(name)
			if old_name is None:
				lineage[name] = SheetLineage.new_sheet(_sheet_rows(new, name), e)
				if any(any(row) for row in _sheet_rows(new, name)):
					changed = True
				continue
			lin = self.lineage[old_name]
			if old_name != name:
				lin.renamed_by = e
				changed = True
			if _step_sheet(lin, _sheet_rows(prev, old_name), _sheet_rows(new, name), self.cfg, e):
				changed = True
			lineage[name] = lin
		for name in gone:
			if name in renamed.values():
				continue
			lin = self.lineage[name]
			changed = True
			if lin.base is not None:
				self.removed_sheets[lin.base] = e
		self.lineage = lineage
		self.cur = new
		self.steps += 1
		if changed:
			self.changed_steps += 1
			self.last_editor = e
		return changed

	# ----------------------------------------------------------------- report

	def finalize(self, baseline: dict, include_hidden: bool = False) -> dict:
		"""Compare the current snapshot with the baseline and attribute each change."""
		fallback = self.last_editor if self.last_editor is not None else (len(self.editors) - 1 if self.editors else None)
		items = []
		referenced_bases = set()
		for name in self.cur["order"]:
			lin = self.lineage[name]
			sheet = self.cur["sheets"][name]
			hidden = sheet.get("hidden") and not include_hidden
			if lin.base is None:
				rows = sheet["rows"]
				count = sum(1 for r in range(len(rows)) if r != self.cfg.header_index and any(rows[r]))
				if count and not hidden:
					items.append({"sheet": name, "kind": "sheet_added", "rows": count, "by": lin.added_by if lin.added_by is not None else fallback})
				continue
			referenced_bases.add(lin.base)
			if hidden:
				continue
			items.extend(self._finalize_sheet(name, lin, baseline, fallback))
		for base_name in baseline["order"]:
			if base_name in referenced_bases:
				continue
			if baseline["sheets"][base_name].get("hidden") and not include_hidden:
				continue
			items.append({"sheet": base_name, "kind": "sheet_removed", "by": self.removed_sheets.get(base_name, fallback)})
		return build_report(items, self.editors)

	def _finalize_sheet(self, name: str, lin: SheetLineage, baseline: dict, fallback) -> list:
		cfg, h = self.cfg, self.cfg.header_index
		base_rows = _sheet_rows(baseline, lin.base)
		new_rows = _sheet_rows(self.cur, name)
		width = max(len(lin.col_anc), _width(new_rows))
		col_anc = _extend_col_anc(lin, width)

		def new_header(c):
			return _cell(new_rows, h, c) if h is not None else ""

		def base_header(b):
			return _cell(base_rows, h, b) if h is not None else ""

		def new_col_name(c):
			return new_header(c) or f"Column {column_letter(c)}"

		def base_col_name(b):
			return base_header(b) or f"Column {column_letter(b)}"

		head, rows_out = [], []
		if lin.base != name:
			head.append({"sheet": name, "kind": "sheet_renamed", "old": lin.base, "by": lin.renamed_by if lin.renamed_by is not None else fallback})

		# Columns
		referenced_cols = {a for a in col_anc if a is not None}
		data_rows = [r for r in range(len(new_rows)) if r != h]
		for c, b in enumerate(col_anc):
			if b is None:
				header = new_header(c)
				if header and not cfg.ignored(header):
					filled = sum(1 for r in data_rows if _cell(new_rows, r, c))
					head.append({"sheet": name, "kind": "col_added", "name": header, "filled": filled, "by": lin.col_added_by.get(c, fallback)})
			elif h is not None and _norm(new_header(c)) != _norm(base_header(b)) and not cfg.ignored(new_header(c)):
				head.append({"sheet": name, "kind": "col_renamed", "old": base_col_name(b), "new": new_col_name(c), "by": lin.cell_by.get((h, c), fallback)})
		for b in range(lin.base_width):
			header = base_header(b)
			if b not in referenced_cols and header and not cfg.ignored(header):
				head.append({"sheet": name, "kind": "col_removed", "name": header, "by": lin.col_removed_by.get(b, fallback)})

		compare = [
			(c, b)
			for c, b in enumerate(col_anc)
			if b is not None and not cfg.ignored(new_header(c)) and not cfg.ignored(base_header(b))
		]
		content_new = [c for c in range(width) if not cfg.ignored(new_header(c))]
		content_base = [b for b in range(max(lin.base_width, _width(base_rows))) if not cfg.ignored(base_header(b))]
		label_new = list(cfg.label_columns)
		label_base = [col_anc[c] if c < len(col_anc) and col_anc[c] is not None else c for c in cfg.label_columns]

		# Rows
		final_anc, added = {}, []
		for r, b in enumerate(lin.row_anc):
			if r == h:
				continue
			if b is None:
				if _has_content(new_rows, r, content_new):
					added.append(r)
				continue
			final_anc[r] = b
		referenced_rows = set(final_anc.values())
		removed = [
			b
			for b in range(len(base_rows))
			if b != h and b not in referenced_rows and _has_content(base_rows, b, content_base)
		]

		# Rows deleted in one step and typed again in another are the same row.
		if added and removed:
			new_t = {r: tuple(_cell(new_rows, r, c) for c, _ in compare) for r in added}
			base_t = {b: tuple(_cell(base_rows, b, bc) for _, bc in compare) for b in removed}
			by_content = defaultdict(deque)
			for b in removed:
				by_content[base_t[b]].append(b)
			paired = {}
			for r in added:
				queue = by_content.get(new_t[r])
				if queue:
					paired[r] = queue.popleft()
			rest_new = [r for r in added if r not in paired]
			taken = set(paired.values())
			rest_base = [b for b in removed if b not in taken]
			if rest_new and rest_base and len(rest_new) * len(rest_base) <= GLOBAL_PAIR_LIMIT:
				candidates = []
				for r in rest_new:
					label_r = _label(new_rows, r, label_new)
					for b in rest_base:
						s = similarity(new_t[r], base_t[b])
						if label_r and label_r == _label(base_rows, b, label_base):
							s += LABEL_BONUS
						if s >= PAIR_MOVED:
							candidates.append((-s, r, b))
				candidates.sort()
				for _, r, b in candidates:
					if r in paired or b in taken:
						continue
					paired[r] = b
					taken.add(b)
			for r, b in paired.items():
				final_anc[r] = b
			added = [r for r in added if r not in paired]
			removed = [b for b in removed if b not in taken]

		ordered = sorted(final_anc)
		seq = [final_anc[r] for r in ordered]
		keep = lis_positions(seq)
		moved_rows = [ordered[i] for i in range(len(ordered)) if i not in keep]
		moved_set = set(moved_rows)

		for r in ordered:
			b = final_anc[r]
			by_editor = defaultdict(list)
			for c, bc in compare:
				new_v, old_v = _cell(new_rows, r, c), _cell(base_rows, b, bc)
				if new_v != old_v:
					who = lin.cell_by.get((r, c))
					if who is None:
						who = lin.row_added_by.get(r, fallback)
					by_editor[who].append({"col": new_col_name(c), "old": old_v, "new": new_v})
			for who, cells in by_editor.items():
				rows_out.append({
					"sheet": name, "kind": "cell", "row": r + 1, "old_row": b + 1,
					"moved": r in moved_set, "label": _label(new_rows, r, label_new), "cells": cells, "by": who,
				})
		for r in added:
			values = [[new_col_name(c), _cell(new_rows, r, c)] for c in content_new if _cell(new_rows, r, c)]
			rows_out.append({"sheet": name, "kind": "row_added", "row": r + 1, "label": _label(new_rows, r, label_new), "values": values, "by": lin.row_added_by.get(r, fallback)})
		rows_out.sort(key=lambda item: item["row"])
		for b in removed:
			values = [[base_col_name(bc), _cell(base_rows, b, bc)] for bc in content_base if _cell(base_rows, b, bc)]
			rows_out.append({"sheet": name, "kind": "row_removed", "row": b + 1, "label": _label(base_rows, b, label_base), "values": values, "by": lin.row_removed_by.get(b, fallback)})

		moves = []
		if moved_rows:
			mover = lin.moved_by[-1] if lin.moved_by else lin.row_added_by.get(moved_rows[0], fallback)
			if len(moved_rows) <= LIST_MOVED_ROWS:
				changed_rows = {item["row"] for item in rows_out if item["kind"] == "cell"}
				for r in moved_rows:
					if r + 1 in changed_rows:
						continue  # the edit line already says where it came from
					moves.append({"sheet": name, "kind": "row_moved", "row": r + 1, "old_row": final_anc[r] + 1, "label": _label(new_rows, r, label_new), "by": mover})
			else:
				moves.append({"sheet": name, "kind": "rows_moved", "count": len(moved_rows), "by": mover})
		return head + moves + rows_out

	# ----------------------------------------------------------------- storage

	def to_dict(self) -> dict:
		return {
			"v": SNAPSHOT_VERSION,
			"cur": self.cur,
			"lineage": {name: lin.to_dict() for name, lin in self.lineage.items()},
			"editors": self.editors,
			"removed_sheets": self.removed_sheets,
			"last_editor": self.last_editor,
			"steps": self.steps,
			"changed_steps": self.changed_steps,
			"meta": self.meta,
		}

	@classmethod
	def from_dict(cls, d: dict, cfg: DiffConfig) -> Session:
		return cls(
			cfg,
			d["cur"],
			{name: SheetLineage.from_dict(x) for name, x in d["lineage"].items()},
			editors=d.get("editors"),
			removed_sheets=d.get("removed_sheets"),
			last_editor=d.get("last_editor"),
			steps=d.get("steps", 0),
			changed_steps=d.get("changed_steps", 0),
			meta=d.get("meta"),
		)


# --------------------------------------------------------------------------- report


def build_report(items: list, editors: list) -> dict:
	"""Wrap items with counts. Each item's ``by`` is an index into ``editors``."""
	counts = defaultdict(int)
	for item in items:
		kind = item["kind"]
		if kind == "cell":
			counts["cells"] += len(item["cells"])
			counts["rows_changed"] += 1
		elif kind == "rows_moved":
			counts["rows_moved"] += item["count"]
		elif kind == "row_moved":
			counts["rows_moved"] += 1
		else:
			counts[kind] += 1
	used = sorted({item["by"] for item in items if item.get("by") is not None})
	return {
		"items": items,
		"editors": editors,
		"active_editors": used,
		"counts": dict(counts),
		"total": sum(len(i["cells"]) if i["kind"] == "cell" else 1 for i in items),
	}


def describe_item(item: dict, editors: list | None = None) -> str:
	"""One plain-text line for the change log."""
	sheet, kind = item["sheet"], item["kind"]
	label = f" · {item['label']}" if item.get("label") else ""
	if kind == "cell":
		where = f"Row {item['row']}{label}"
		if item.get("moved") or item["row"] != item.get("old_row", item["row"]):
			where += f" (was row {item['old_row']})"
		parts = [f"{c['col']}: {c['old'] or '(empty)'} → {c['new'] or '(empty)'}" for c in item["cells"]]
		text = f"{where} — " + "; ".join(parts)
	elif kind == "row_added":
		text = f"Row {item['row']} added{label} — " + ", ".join(f"{k}: {v}" for k, v in item["values"])
	elif kind == "row_removed":
		text = f"Row {item['row']} removed{label} — " + ", ".join(f"{k}: {v}" for k, v in item["values"])
	elif kind == "row_moved":
		text = f"Row {item['row']}{label} moved from row {item['old_row']}"
	elif kind == "rows_moved":
		text = f"{item['count']} rows were sorted or moved"
	elif kind == "col_added":
		text = f"Column added: {item['name']}"
	elif kind == "col_removed":
		text = f"Column removed: {item['name']}"
	elif kind == "col_renamed":
		text = f"Column renamed: {item['old']} → {item['new']}"
	elif kind == "sheet_added":
		text = f"Sheet added ({item['rows']} rows)"
	elif kind == "sheet_removed":
		text = "Sheet removed"
	elif kind == "sheet_renamed":
		text = f"Sheet renamed from {item['old']}"
	else:
		text = kind
	who = ""
	if editors and item.get("by") is not None and item["by"] < len(editors):
		who = f" (by {editors[item['by']]['name']})"
	return f"[{sheet}] {text}{who}"


def report_text(report: dict) -> str:
	return "\n".join(describe_item(item, report["editors"]) for item in report["items"])
