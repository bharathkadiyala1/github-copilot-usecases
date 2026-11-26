"""Schema resolver: understand ANY header names and layout, and turn them into the one model the rest of the app uses.

Standard library only. No business rules live here; it only decides which column means what.

How a table is understood
  1. Every sheet is searched for header rows (any row, any sheet, any order of sheets).
  2. Each column gets a confidence for each known field from three signals:
        header words   ("Src Tbl", "Target Field Name", "Transformation Rule" ...), including typos and two-row headers
        column content (values that look like IDs, SQL types, PK/FK, INNER/LEFT, a = b ...)
        position       (when two columns tie, source comes before target)
  3. The best header row for the mapping table and for the joins/filters table is chosen by score, so tab names and tab
     order do not matter, and both tables may share one sheet.
  4. Cell values are normalised (rule types, join words, key flags, numbers) and a report says exactly what was decided.
Nothing is guessed silently: the report lists every column with its confidence, what was left over, and what is missing.
"""
import csv
import io
import json
import re
from difflib import SequenceMatcher
from functools import lru_cache

# ------------------------------------------------------------------ the fields the app understands
# (field, label written to Excel, phrases that mean it)
MAP_SPEC = [
    ("id", "Map ID", ["map id", "mapping id", "id", "number", "ref", "reference", "row id", "field id", "line"]),
    ("source_system", "Source System", ["source system", "system", "source application", "source app", "source platform", "source db", "source database"]),
    ("source_schema", "Source Schema", ["source schema", "source owner", "source library", "source namespace", "source catalog"]),
    ("source_table", "Source Table", ["source table", "source entity", "source object", "source dataset", "source view", "from table"]),
    ("source_column", "Source Column", ["source column", "source field", "source attribute", "source element", "from column", "source"]),
    ("source_type", "Source Data Type", ["source type", "source data type", "source format"]),
    ("target_schema", "Target Schema", ["target schema", "target owner", "target library", "target namespace", "target catalog"]),
    ("target_table", "Target Table", ["target table", "target entity", "target object", "target dataset", "target view"]),
    ("target_column", "Target Column", ["target column", "target field", "target attribute", "target element", "target"]),
    ("target_type", "Target Data Type", ["target type", "target data type", "target format"]),
    ("key", "Key", ["key", "key type", "pk fk", "pk", "primary key", "constraint", "key indicator", "key flag"]),
    ("logic", "Transformation / Business Logic", ["transformation", "transformation logic", "transformation rule", "business logic", "business rule",
                                                  "logic", "rule", "derivation", "mapping rule", "mapping logic", "formula", "transform", "expression"]),
    ("nullable", "Nullable", ["nullable", "null", "nulls allowed", "null allowed", "optional", "is nullable", "null flag"]),
    ("notes", "Notes", ["notes", "note", "comments", "comment", "remarks", "remark", "description", "additional info"]),
]
RULE_SPEC = [
    ("seq", "Seq", ["seq", "sequence", "step", "order", "number", "execution order", "step number", "rule number", "rule id", "sequence number"]),
    ("type", "Type", ["type", "rule type", "step type", "category", "kind", "operation", "operation type"]),
    ("name", "Rule Name", ["rule name", "name", "step name", "title", "rule", "rule description"]),
    ("left", "Left Table (Alias)", ["left table", "left", "left side", "from table", "parent table", "base table alias"]),
    ("right", "Right Table (Alias)", ["right table", "right", "right side", "join table", "child table", "joined table", "lookup table"]),
    ("join_type", "Join Type", ["join type", "join kind", "join method", "type of join"]),
    ("expr", "Join Condition / Filter Expression", ["join condition", "filter expression", "condition", "expression", "filter condition", "predicate", "criteria",
                                                    "join clause", "on clause", "filter", "sql", "clause", "join expression", "join filter"]),
    ("logic", "Business Logic / Rationale", ["business logic", "rationale", "reason", "why", "explanation", "description", "purpose", "business rule", "comments", "notes", "logic"]),
    ("applies", "Applies To Map IDs", ["applies to", "applies to map ids", "map ids", "mapping ids", "applies to columns", "columns affected", "affects", "used by", "map id"]),
]
MAP_HEADERS = [(f, label) for f, label, _ in MAP_SPEC]
RULE_HEADERS = [(f, label) for f, label, _ in RULE_SPEC]
SPECS = {"mapping": MAP_SPEC, "rules": RULE_SPEC}
LABELS = {"mapping": dict(MAP_HEADERS), "rules": dict(RULE_HEADERS)}

# a table is usable when it has at least these (any one group from the list)
def _required_ok(kind, fields):
    if kind == "mapping":
        return "target_column" in fields and bool({"source_column", "logic", "source_table"} & set(fields))
    return "expr" in fields or ("type" in fields and "name" in fields)


def _required_text(kind):
    return ("a target column, plus a source column or a transformation rule" if kind == "mapping"
            else "a join/filter expression (or a rule type and rule name)")


# ------------------------------------------------------------------ header words
SYN = {"src": "source", "tgt": "target", "trg": "target", "dest": "target", "destination": "target", "dst": "target", "sink": "target",
       "col": "column", "cols": "column", "columns": "column", "fld": "column", "field": "column", "fields": "column",
       "attribute": "column", "attributes": "column", "attr": "column", "element": "column",
       "tbl": "table", "tables": "table", "entity": "table", "object": "table", "dataset": "table",
       "datatype": "type", "dtype": "type", "types": "type", "dt": "type", "sch": "schema", "sys": "system", "systems": "system",
       "no": "number", "num": "number", "nbr": "number", "seqno": "seq", "sequence": "seq", "notes": "note", "comments": "comment",
       "remarks": "remark", "conditions": "condition", "rules": "rule", "ids": "id"}
STOP = {"the", "of", "a", "an", "and", "or", "to", "is", "are", "in", "for", "if", "with", "by"}
SIDES = {"source", "target"}


def tokens(text):
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text))
    t = t.replace("#", " number ").replace("?", " ")
    out = [SYN.get(w, w) for w in re.findall(r"[a-z0-9]+", t.lower())]
    out = [w for w in out if w not in STOP]
    if "name" in out and len(out) > 1:
        out.remove("name")
    if "data" in out and "type" in out:
        out.remove("data")
    return out


@lru_cache(maxsize=None)
def _tok_eq(a, b):
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.82:
        return 0.8
    return 0.0


def _phrase_score(h, p):
    """How well header tokens h mean phrase tokens p: 1.0 exact, lower for extra words, typos or a bare head word."""
    if not h or not p:
        return 0.0
    if (set(h) & SIDES) - set(p):                  # 'Source Data Type' must never become a plain 'Type'
        return 0.0
    if (set(p) & SIDES) and (set(h) & SIDES) and (set(p) & SIDES) != (set(h) & SIDES):
        return 0.0
    used, fuzzy = set(), False
    for w in p:
        best, hit = 0.0, None
        for k, x in enumerate(h):
            e = _tok_eq(x, w)
            if e > best and k not in used:
                best, hit = e, k
        if best == 0.0:
            break
        used.add(hit)
        fuzzy = fuzzy or best < 1.0
    else:
        extra = len(h) - len(used)
        score = 1.0 if extra == 0 else max(0.8, 0.95 - 0.03 * extra)
        return min(score, 0.84) if fuzzy else score
    if len(h) < len(p) and h[-1] == p[-1]:
        return 0.5                                  # a bare 'Column' / 'Type': probably this, position will decide
    return 0.0


_PHRASES = {k: {f: [tokens(p) for p in phrases] for f, _, phrases in spec} for k, spec in SPECS.items()}


def header_score(kind, field, header_tokens):
    return _header_score(kind, field, tuple(header_tokens))


@lru_cache(maxsize=None)
def _header_score(kind, field, header_tokens):
    header_tokens = list(header_tokens)
    best = 0.0
    for p in _PHRASES[kind][field]:
        sc = _phrase_score(header_tokens, p)
        if p in (["source"], ["target"]):          # a bare 'Source' / 'Target' is probably the column, but not for certain
            sc = min(sc, 0.7)
        best = max(best, sc)
    return best


# ------------------------------------------------------------------ what the values look like
_TYPE = re.compile(r"^\s*(?:var)?char2?|^\s*nvarchar2?|^\s*string|^\s*text|^\s*clob|^\s*blob|^\s*int(?:eger)?\b|^\s*bigint|^\s*smallint|^\s*tinyint|"
                   r"^\s*number|^\s*numeric|^\s*decimal|^\s*dec\b|^\s*float|^\s*double|^\s*real\b|^\s*date|^\s*timestamp|^\s*time\b|^\s*bool|^\s*bit\b|"
                   r"^\s*binary|^\s*varbinary|^\s*long\b|^\s*raw\b|^\s*uuid|^\s*struct|^\s*array", re.I)
_KEYS = {"pk", "fk", "uk", "bk", "sk", "nk", "pf", "primary key", "foreign key", "unique", "unique key", "business key", "surrogate key", "primary", "foreign"}
_NULLS = {"y", "n", "yes", "no", "true", "false", "null", "not null", "nullable", "nn", "notnull", "optional", "required", "mandatory"}
_JOINS = re.compile(r"^\s*(inner|left|right|full|cross|outer|natural|semi|anti)\b", re.I)
_ID = re.compile(r"^[A-Za-z]{1,5}[-_ ]?\d{1,6}$")
_IDLIST = re.compile(r"^\s*(all|[A-Za-z]{0,5}[-_]?\d{1,6})(\s*[,;/&]\s*(all|[A-Za-z]{0,5}[-_]?\d{1,6}))*\s*(\(.*\))?$", re.I)
_EXPR = re.compile(r"=|<|>|\bfrom\b|\bgroup\s+by\b|\bwhere\b|\bin\s*\(|\bis\s+(not\s+)?null\b|\bbetween\b|\blike\b|\bpartition\s+by\b|\binsert\b|\bmerge\b|\bon\b", re.I)
_ALIASED = re.compile(r"\(\s*\w+\s*\)\s*$|^[\w$]+(\.[\w$]+)+$")
_LOGIC = re.compile(r"direct|lookup|case\b|coalesce|default|concat|cast\(|trim|sum\(|count\(|substr|\bif\b|current_|derive|hardcode|constant|move|nvl|round\(", re.I)


def _vals(values):
    return [v.strip()[:300] for v in values if v and v.strip() and v.strip() not in ("-", "—", "n/a", "N/A")]


def _frac(values, test):
    vals = _vals(values)
    return sum(1 for v in vals if test(v)) / len(vals) if vals else None


def content_score(kind, field, values):
    """0..1 how much the values look like this field, or None when the values say nothing."""
    from_rule_type = rule_type
    if kind == "mapping":
        if field == "id":
            vals = _vals(values)
            f = _frac(values, lambda v: bool(_ID.match(v)))
            return None if f is None else (f if len(set(vals)) >= 0.9 * len(vals) else f * 0.5)
        if field in ("source_type", "target_type"):
            return _frac(values, lambda v: bool(_TYPE.match(v)) and len(v) < 40)
        if field == "key":
            f = _frac(values, lambda v: v.lower() in _KEYS)
            return f if f and any(v.lower() in _KEYS for v in _vals(values)) else (0.0 if f is not None else None)
        if field == "nullable":
            return _frac(values, lambda v: v.lower() in _NULLS)
        if field == "logic":
            f = _frac(values, lambda v: bool(_LOGIC.search(v)))
            return None if f is None else min(f, 0.7)
        return None
    if field == "seq":
        vals = _vals(values)
        f = _frac(values, lambda v: bool(re.fullmatch(r"\d+(\.0+)?", v)))
        if f is None:
            return None
        nums = [float(v) for v in vals if re.fullmatch(r"\d+(\.0+)?", v)]
        return f if nums == sorted(nums) else f * 0.6
    if field == "type":
        return _frac(values, lambda v: from_rule_type(v) in RULE_TYPES.values())
    if field == "join_type":
        f = _frac(values, lambda v: bool(_JOINS.match(v)))
        return f if f and any(_JOINS.match(v) for v in _vals(values)) else (0.0 if f is not None else None)
    if field == "expr":
        return _frac(values, lambda v: bool(_EXPR.search(v)))
    if field == "applies":
        f = _frac(values, lambda v: bool(_IDLIST.match(v)))
        return f if f and any(re.search(r"\d", v) for v in _vals(values)) else (0.0 if f is not None else None)
    if field in ("left", "right"):
        f = _frac(values, lambda v: bool(_ALIASED.search(v)))
        return None if f is None else min(f, 0.7)
    return None


def column_score(kind, field, header_tokens, values, cs=False):
    """(score, how). Header words lead; the values confirm, contradict, or stand in when the header says nothing."""
    hs = header_score(kind, field, header_tokens)
    if cs is False:
        cs = content_score(kind, field, values)
    if hs >= 0.5:
        if cs is None:
            return hs, "header"
        if cs >= 0.5:
            return min(1.0, hs + 0.1 * cs), "header"
        if cs < 0.3 and field in ("seq", "type", "join_type", "key", "nullable", "applies", "id", "source_type", "target_type"):
            return hs * 0.7, "header"
        return hs, "header"
    if cs is not None and cs >= 0.8:
        return 0.78 * cs, "content"
    return 0.0, ""


# ------------------------------------------------------------------ value clean-up
RULE_TYPES = {}
for _canon, _words in (("Driver", "driver base basetable main maintable from primary drivingtable sourcedriver driving basetablerule"),
                       ("Join", "join innerjoin leftjoin rightjoin fulljoin outerjoin crossjoin leftouterjoin rightouterjoin fullouterjoin lookup lookupjoin joins"),
                       ("Filter", "filter where condition predicate having selection whereclause filtercondition filters"),
                       ("Dedup", "dedup deduplicate deduplication dedupe distinct removeduplicates deduplicates"),
                       ("Aggregation", "aggregation aggregate groupby grouping summarize rollup aggregations"),
                       ("Target Load", "targetload load insert merge upsert overwrite insertoverwrite target loadrule")):
    for _w in _words.split():
        RULE_TYPES[_w] = _canon


def _flat(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def rule_type(text):
    """'Base Table' -> 'Driver', 'WHERE' -> 'Filter' ... Unknown wording is returned unchanged."""
    return RULE_TYPES.get(_flat(text), str(text).strip())


_JOIN_WORD = re.compile(r"^\s*(inner|left|right|full|cross)(\s+outer)?(\s+join)?\s*$", re.I)


def join_type_word(text):
    m = _JOIN_WORD.match(str(text))
    return m.group(1).upper() if m else str(text).strip()


def _key_word(text):
    t = str(text).strip()
    low = t.lower()
    return {"primary key": "PK", "primary": "PK", "foreign key": "FK", "foreign": "FK", "unique": "UK", "unique key": "UK"}.get(low, t.upper() if low in _KEYS else t)


def _null_word(text):
    t = str(text).strip().lower()
    if t in ("y", "yes", "true", "null", "nullable", "optional"):
        return "Y"
    if t in ("n", "no", "false", "not null", "notnull", "nn", "required", "mandatory"):
        return "N"
    return str(text).strip()


def _number(text):
    t = str(text).strip()
    return re.sub(r"\.0+$", "", t) if re.fullmatch(r"-?\d+\.0+", t) else t


def clean_row(kind, item):
    if kind == "mapping":
        if item.get("key"):
            item["key"] = _key_word(item["key"])
        if item.get("nullable"):
            item["nullable"] = _null_word(item["nullable"])
        item["id"] = _number(item.get("id", ""))
        return item
    raw = item.get("type", "")
    item["type"] = rule_type(raw)
    if item["type"] == "Join" and re.search(r"(inner|left|right|full|cross|outer)", raw, re.I) and item.get("join_type", "") in ("", "-"):
        item["join_type"] = join_type_word(re.sub(r"\bjoin\b", "", raw, flags=re.I))
    elif item.get("join_type"):
        item["join_type"] = join_type_word(item["join_type"])
    expr = item.get("expr", "").lstrip().lower()
    if not item["type"] and expr:                    # no Type column at all: say what the expression is
        item["type"] = ("Driver" if expr.startswith("from") else "Aggregation" if expr.startswith("group by")
                        else "Filter" if expr.startswith("where") else "Join" if (re.search(r"\bjoin\b", expr) or _JOIN_WORD.match(item.get("join_type", ""))) else "")
    item["seq"] = _number(item.get("seq", ""))
    return item


# ------------------------------------------------------------------ grids from text (CSV, markdown, JSON)
def md_tables(text):
    lines = [l for l in text.replace("\r", "").split("\n") if not l.strip().startswith("```")]
    tables, i = [], 0
    split = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    while i < len(lines) - 1:
        if lines[i].count("|") >= 2 and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head, rows, i = split(lines[i]), [], i + 2
            while i < len(lines) and lines[i].count("|") >= 2:
                rows.append(split(lines[i]))
                i += 1
            tables.append([head] + rows)
        else:
            i += 1
    return tables


def _json_grids(obj):
    def grid(rows):
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        return [keys] + [[("" if r.get(k) is None else str(r.get(k))) for k in keys] for r in rows]
    if isinstance(obj, list) and obj and all(isinstance(r, dict) for r in obj):
        return {"json": grid(obj)}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
                out[str(k)] = grid(v)
        return out
    return {}


def grids_from_text(text):
    """Any pasted text -> {name: rows}. Tries JSON, then markdown tables, then CSV / tab / semicolon text."""
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", body, re.S)
    for candidate in ([fenced.group(1)] if fenced else []) + ([body] if body[:1] in "[{" else []):
        try:
            grids = _json_grids(json.loads(candidate))
            if grids:
                return grids
        except ValueError:
            pass
    tables = md_tables(text)
    if tables:
        return {"table%d" % (n + 1): t for n, t in enumerate(tables)}
    lines = [l for l in body.replace("\r", "").split("\n") if not l.strip().startswith("```")]
    sample = "\n".join(lines[:20])
    delim = max(",\t;|", key=sample.count)
    if sample.count(delim) >= 2:
        return {"text": [[c.strip() for c in r] for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]}
    return {}


# ------------------------------------------------------------------ find and read the tables
def _blank(row):
    return not any((c or "").strip() for c in row)


def _col_letter(i):
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letter):
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _header_like(row):
    cells = [c.strip() for c in row if c and c.strip()]
    if len(cells) < 2:
        return False
    texty = [c for c in cells if len(c) <= 60 and not re.fullmatch(r"[\d.,\-\s]+", c)]
    return len(texty) >= max(2, 0.7 * len(cells))


def _region_end(rows, start, hard_end=None):
    """First row after the table: two blank rows in a row, or the end of the sheet / the next table's header."""
    blanks, last = 0, start - 1
    stop = len(rows) if hard_end is None else min(hard_end, len(rows))
    for k in range(start, stop):
        if _blank(rows[k]):
            blanks += 1
            if blanks >= 2:
                break
        else:
            blanks, last = 0, k
    return last + 1


def _band_text(rows, i, j):
    """Text of a group band one row above the header ('Source' above 'Table'/'Column'), carried across its columns."""
    if i == 0:
        return ""
    band = rows[i - 1]
    filled = [k for k, c in enumerate(band) if (c or "").strip()]
    if len(filled) < 2:
        return ""
    k = max((x for x in filled if x <= j), default=None)
    return band[k].strip() if k is not None and len(band[k].strip()) <= 30 else ""


def _score_table(kind, sheet, rows, i, forced=None):
    """Try row i as the header row of a `kind` table. Returns the best one-to-one column assignment and its score."""
    header = rows[i]
    end = _region_end(rows, i + 1, i + 1 + 400)
    body = rows[i + 1:end]
    ncols = max([len(header)] + [len(r) for r in body[:200]]) if body else len(header)
    values = [[(r[j] if j < len(r) else "") for r in body[:200]] for j in range(ncols)]
    spec = [f for f, _, _ in SPECS[kind]]
    pairs = []
    for j in range(ncols):
        cell = (header[j] if j < len(header) else "").strip()
        variants = [tokens(cell)] if cell else [[]]
        band = _band_text(rows, i, j)
        if band:
            variants.append(tokens(band + " " + cell))
        for field in spec:
            cs = content_score(kind, field, values[j])
            best = max((column_score(kind, field, v, values[j], cs) for v in variants), key=lambda s: s[0])
            if best[0] >= 0.5:
                pairs.append((-round(best[0], 3), spec.index(field), j, field, best))
    pairs.sort()
    assign, used = {}, set()
    for _, _, j, field, (score, how) in pairs:
        if j in used or any(f == field for f, _, _ in assign.values()):
            continue
        assign[j] = (field, score, how)
        used.add(j)
    if forced is not None:
        assign = {j: (f, 1.0, "chosen") for j, f in forced.items() if f in spec}
    fields = {f for f, _, _ in assign.values()}
    ok = _required_ok(kind, fields)
    total = sum(s for _, s, _ in assign.values()) + (3 if ok else 0)
    return {"kind": kind, "sheet": sheet, "header_row": i, "end": end, "assign": assign, "ok": ok, "score": total, "ncols": ncols}


def _level(score):
    return "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"


class Unresolved(ValueError):
    """The layout could not be understood on its own. `report` says what was seen so a person or Copilot can finish the job."""

    def __init__(self, message, report=None, grids=None):
        super().__init__(message)
        self.report, self.grids = report or {}, grids or {}


def _describe(grids, table, kind):
    rows = grids[table["sheet"]]
    header = rows[table["header_row"]]
    cols = []
    for j in range(table["ncols"]):
        text = (header[j] if j < len(header) else "").strip()
        sample = [r[j].strip() for r in rows[table["header_row"] + 1:table["end"]] if j < len(r) and r[j].strip()][:3]
        if j in table["assign"]:
            field, score, how = table["assign"][j]
            cols.append({"index": j, "letter": _col_letter(j), "header": text, "field": field, "label": LABELS[kind][field],
                         "confidence": round(score, 2), "level": _level(score), "how": how, "sample": sample})
        elif text or sample:
            cols.append({"index": j, "letter": _col_letter(j), "header": text, "field": "", "label": "", "confidence": 0, "level": "unused", "how": "", "sample": sample})
    fields = {f for f, _, _ in table["assign"].values()}
    return {"kind": kind, "sheet": table["sheet"], "header_row": table["header_row"] + 1, "first_row": table["header_row"] + 2,
            "last_row": table["end"], "columns": cols, "usable": table["ok"],
            "missing": [LABELS[kind][f] for f, _, _ in SPECS[kind] if f not in fields]}


def _pick(grids, kind, taken, forced):
    """Best header row for `kind` across all sheets, skipping rows already used by the other table."""
    if forced and forced.get("skip"):                 # a person said this file has no such table
        return None
    if forced and (forced.get("columns") or forced.get("header_row")):
        sheet = forced.get("sheet") if forced.get("sheet") in grids else None
        try:
            row = int(forced.get("header_row") or 0) - 1
        except (TypeError, ValueError):
            row = -1
        spec = {f for f, _, _ in SPECS[kind]}
        cols = {col_index(k): v for k, v in (forced.get("columns") or {}).items() if v in spec and re.fullmatch(r"[A-Za-z]{1,3}", str(k))} if forced.get("columns") else None
        for name, rows in ([(sheet, grids[sheet])] if sheet else grids.items()):
            if 0 <= row < len(rows) and not _blank(rows[row]):
                return _score_table(kind, name, rows, row, forced=cols)
        if cols:                                       # no row given: use the most header-like row, with the columns as chosen
            base = _pick(grids, kind, taken, None)
            if base:
                return _score_table(kind, base["sheet"], grids[base["sheet"]], base["header_row"], forced=cols)
    best = None
    for name, rows in grids.items():
        for i, row in enumerate(rows[:80]):
            if (name, i) in taken or not _header_like(row):
                continue
            t = _score_table(kind, name, rows, i)
            if best is None or (t["ok"], t["score"]) > (best["ok"], best["score"]):
                best = t
    return best


def _extract(grids, table, kind, stop_at):
    rows = grids[table["sheet"]]
    end = min(table["end"], stop_at) if stop_at else table["end"]
    fields = {j: f for j, (f, _, _) in table["assign"].items()}
    names = [f for f, _, _ in SPECS[kind]]
    out, extras, dropped, seen = [], [], [], 0
    header_text = {_flat(rows[table["header_row"]][j]) for j in fields if j < len(rows[table["header_row"]])}
    for n in range(table["header_row"] + 1, end):
        r = rows[n]
        if _blank(r):
            continue
        seen += 1
        item = {f: "" for f in names}
        for j, f in fields.items():
            item[f] = r[j].strip() if j < len(r) and r[j] else ""
        if {_flat(item[f]) for f in fields.values() if item[f]} <= header_text and any(item.values()):     # a repeated header row
            seen -= 1
            continue
        keep = (item["target_column"] or item["id"]) if kind == "mapping" else (item["type"] or item["expr"])
        if not keep:
            if sum(1 for c in r if (c or "").strip()) > 1:
                dropped.append(n + 1)
            else:
                seen -= 1                              # a lone title or note line is not a data row
            continue
        out.append(clean_row(kind, item))
        extras.append({(rows[table["header_row"]][j].strip() or _col_letter(j)): r[j].strip() for j in range(len(r))
                       if j not in fields and r[j].strip() and j < len(rows[table["header_row"]]) and rows[table["header_row"]][j].strip()})
    return out, extras, dropped, seen


def resolve(grids, overrides=None):
    """grids {sheet: rows of text} -> (model, report). Raises Unresolved when no usable mapping table exists.

    overrides = {"mapping": {"sheet", "header_row" (1-based), "columns": {"A": "source_table", ...}}, "rules": {...}}
    comes from the Review screen or from Copilot's schema answer.
    """
    overrides = overrides or {}
    grids = {k: v for k, v in grids.items() if v}
    if not grids:
        raise Unresolved("The file has no data to read.", grids=grids)
    mapping_t = _pick(grids, "mapping", set(), overrides.get("mapping"))
    taken = {(mapping_t["sheet"], mapping_t["header_row"])} if mapping_t else set()
    rules_t = _pick(grids, "rules", taken, overrides.get("rules"))
    if mapping_t and rules_t and (mapping_t["sheet"], mapping_t["header_row"]) == (rules_t["sheet"], rules_t["header_row"]):
        rules_t = None
    # a rules table that is only a weak guess is dropped rather than risk reading mapping rows as rules
    if rules_t and not rules_t["ok"] and not (overrides.get("rules") or {}).get("columns"):
        rules_t = None
    if not mapping_t or not mapping_t["ok"]:
        report = {"tables": [_describe(grids, mapping_t, "mapping")] if mapping_t else [], "warnings": [], "needs_review": True, "resolved": False}
        if mapping_t:
            guess = _pick(grids, "rules", {(mapping_t["sheet"], mapping_t["header_row"])}, overrides.get("rules"))
            if guess:
                report["tables"].append(_describe(grids, guess, "rules"))
        seen = ""
        if mapping_t:
            found = [c["label"] for c in report["tables"][0]["columns"] if c["field"]]
            seen = " Best match was sheet '%s' row %d: understood %s." % (mapping_t["sheet"], mapping_t["header_row"] + 1, ", ".join(found) or "no columns")
        raise Unresolved("Could not find a mapping table. It needs %s.%s" % (_required_text("mapping"), seen), report, grids)

    stop_map = rules_t["header_row"] if rules_t and rules_t["sheet"] == mapping_t["sheet"] and rules_t["header_row"] > mapping_t["header_row"] else None
    stop_rule = mapping_t["header_row"] if rules_t and rules_t["sheet"] == mapping_t["sheet"] and mapping_t["header_row"] > rules_t["header_row"] else None
    mappings, map_extras, map_dropped, map_seen = _extract(grids, mapping_t, "mapping", stop_map)
    rules, rule_extras, rule_dropped, rule_seen = ([], [], [], 0) if not rules_t else _extract(grids, rules_t, "rules", stop_rule)

    tables = [_describe(grids, mapping_t, "mapping")]
    warnings = []
    if rules_t:
        tables.append(_describe(grids, rules_t, "rules"))
    else:
        warnings.append("No joins/filters table was found. Code can still be built from the mapping alone, but joins and filters will be missing.")
    for t, dropped in ((tables[0], map_dropped), (tables[-1] if rules_t else None, rule_dropped)):
        if t and dropped:
            warnings.append("%s table: %d row(s) were skipped because they had no %s (rows %s)." % (
                "Mapping" if t["kind"] == "mapping" else "Joins/filters", len(dropped), "target column or Map ID" if t["kind"] == "mapping" else "type or expression",
                ", ".join(str(d) for d in dropped[:8])))
    for t in tables:
        t["rows"] = len(mappings) if t["kind"] == "mapping" else len(rules)
        t["rows_seen"] = map_seen if t["kind"] == "mapping" else rule_seen
        unused = [c for c in t["columns"] if c["level"] == "unused" and c["header"]]
        if unused:
            warnings.append("Not used (kept as extra columns): " + ", ".join("%s (%s)" % (c["header"], c["letter"]) for c in unused))
    weak = [c for t in tables for c in t["columns"] if c["field"] and c["level"] != "high"]
    needs_review = bool(weak) or any(t["missing"] and t["kind"] == "mapping" and ("Target Column" in t["missing"] or "Source Column" in t["missing"]) for t in tables)
    report = {"tables": tables, "warnings": warnings, "needs_review": needs_review, "resolved": True,
              "rows_in": map_seen + rule_seen, "rows_out": len(mappings) + len(rules)}
    model = {"mappings": mappings, "rules": rules, "reference_sql": "", "extras": {"mappings": map_extras, "rules": rule_extras}, "detection": report}
    return model, report


def sample_view(grids, rows=6, width=60, focus=None):
    """A compact picture of each sheet (first rows, plus the rows around any suspected header), trimmed. Used for the Copilot prompt."""
    focus = focus or {}
    out = {}
    for name, grid in grids.items():
        want = set()
        shown = 0
        for n, row in enumerate(grid[:200]):
            if not _blank(row) and shown < rows:
                want.add(n)
                shown += 1
        for h in focus.get(name, []):
            want.update(n for n in range(max(0, h - 1), h + 4) if n < len(grid))
        out[name] = [{"row": n + 1, "cells": {_col_letter(j): c.strip()[:width] for j, c in enumerate(grid[n]) if c and c.strip()}}
                     for n in sorted(want) if not _blank(grid[n])]
    return out


def overrides_from_reply(text):
    """Copilot's JSON answer -> overrides for resolve(). Unknown field names are ignored. Raises ValueError if there is no usable JSON."""
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body, re.S)
    raw = fenced.group(1) if fenced else (body[body.index("{"):body.rindex("}") + 1] if "{" in body and "}" in body else "")
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("Copilot's reply did not contain the JSON answer. Paste the whole reply, or set the columns by hand.")
    out = {}
    for key, kind in (("mapping", "mapping"), ("mappings", "mapping"), ("rules", "rules"), ("joins_filters", "rules")):
        part = data.get(key)
        if not isinstance(part, dict):
            continue
        valid = {f for f, _, _ in SPECS[kind]}
        cols = part.get("columns") or {}
        if isinstance(cols, list):
            cols = {c.get("column") or c.get("letter"): c.get("field") for c in cols if isinstance(c, dict)}
        cols = {str(k).strip().upper(): v for k, v in cols.items() if k and v in valid and re.fullmatch(r"[A-Za-z]{1,3}", str(k).strip())}
        if cols:
            out[kind] = {"sheet": part.get("sheet"), "header_row": part.get("header_row"), "columns": cols}
    if not out:
        raise ValueError("Copilot's answer named no columns. Set the columns by hand below.")
    return out
