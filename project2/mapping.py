"""Mapping documents: read .xlsx, understand it, check it, write it back, and check code against it.

Standard library only. No business rules live here; it only reads structure and compares text.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

MAP_HEADERS = [("id", "Map ID"), ("source_system", "Source System"), ("source_schema", "Source Schema"),
               ("source_table", "Source Table"), ("source_column", "Source Column"), ("source_type", "Source Data Type"),
               ("target_schema", "Target Schema"), ("target_table", "Target Table"), ("target_column", "Target Column"),
               ("target_type", "Target Data Type"), ("key", "Key"), ("logic", "Transformation / Business Logic"),
               ("nullable", "Nullable"), ("notes", "Notes")]
RULE_HEADERS = [("seq", "Seq"), ("type", "Type"), ("name", "Rule Name"), ("left", "Left Table (Alias)"),
                ("right", "Right Table (Alias)"), ("join_type", "Join Type"), ("expr", "Join Condition / Filter Expression"),
                ("logic", "Business Logic / Rationale"), ("applies", "Applies To Map IDs")]
SHEET_NAMES = ("Source_Target_Mapping", "Joins_Filters")


# ------------------------------------------------------------------ read .xlsx
def _col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data):
    """bytes of an .xlsx -> {sheet name: [[cell text, ...], ...]}. Formulas are read as their last saved value."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ValueError("That file is not a valid .xlsx workbook.")
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iterfind("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheets = {}
    for sheet in workbook.iterfind("m:sheets/m:sheet", NS):
        target = rels[sheet.get(REL)].lstrip("/")
        path = target if target.startswith("xl/") else "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(path)).iterfind(".//m:row", NS):
            cells = {}
            for c in row.iterfind("m:c", NS):
                kind, v = c.get("t"), c.find("m:v", NS)
                if kind == "s" and v is not None:
                    text = shared[int(v.text)]
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter("{%s}t" % NS["m"]))
                else:
                    text = v.text if v is not None and v.text is not None else ""
                cells[_col_index(c.get("r"))] = text
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        sheets[sheet.get("name")] = rows
    return sheets


# ------------------------------------------------------------------ understand the layout
def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _map_field(h):
    table = {"mapid": "id", "id": "id", "mappingid": "id", "sourcesystem": "source_system", "sourceschema": "source_schema",
             "sourcetable": "source_table", "sourcecolumn": "source_column", "sourcedatatype": "source_type", "sourcetype": "source_type",
             "targetschema": "target_schema", "targettable": "target_table", "targetcolumn": "target_column",
             "targetdatatype": "target_type", "targettype": "target_type", "key": "key", "nullable": "nullable", "notes": "notes", "comments": "notes"}
    if h in table:
        return table[h]
    return "logic" if ("transformation" in h or "logic" in h) else None


def _rule_field(h):
    if h in ("seq", "sequence", "step", "order"):
        return "seq"
    if h in ("type", "ruletype"):
        return "type"
    if h in ("rulename", "name"):
        return "name"
    if h.startswith("lefttable"):
        return "left"
    if h.startswith("righttable"):
        return "right"
    if h == "jointype":
        return "join_type"
    if "condition" in h or "expression" in h:
        return "expr"
    if "rationale" in h or "logic" in h:
        return "logic"
    if "appliesto" in h or h == "mapids":
        return "applies"
    return None


def _table_from(rows, field_of, needed, keep):
    """Find the header row (it holds all `needed` fields) and return the rows below it as dicts."""
    for i, row in enumerate(rows):
        fields = {}
        for j, cell in enumerate(row):
            f = field_of(_norm(cell))
            if f and f not in fields:
                fields[f] = j
        if all(n in fields for n in needed):
            out = []
            for r in rows[i + 1:]:
                item = {f: (r[j].strip() if j < len(r) else "") for f, j in fields.items()}
                if keep(item):
                    out.append(item)
            return out
    return None


def parse_workbook(sheets):
    """Find the mapping table, the joins/filters table and any reference SQL in any sheet."""
    mappings, rules, reference = None, None, ""
    for rows in sheets.values():
        if mappings is None:
            mappings = _table_from(rows, _map_field, ("target_column", "source_column"), lambda i: i.get("target_column") or i.get("id"))
        if rules is None:
            rules = _table_from(rows, _rule_field, ("seq", "expr"), lambda i: i.get("type") or i.get("expr"))
        for row in rows:
            for cell in row:
                low = cell.lower()
                if len(cell) > 80 and "select" in low and "from" in low and re.match(r"\s*(insert|with|select|merge|create)", low):
                    reference = reference or cell
    if mappings is None:
        raise ValueError("Could not find a mapping table. It needs columns named 'Source Column' and 'Target Column'.")
    for m in mappings:
        for f, _ in MAP_HEADERS:
            m.setdefault(f, "")
    rules = rules or []
    for r in rules:
        for f, _ in RULE_HEADERS:
            r.setdefault(f, "")
    return {"mappings": mappings, "rules": rules, "reference_sql": reference}


def load_xlsx(data):
    return parse_workbook(read_xlsx(data))


# ------------------------------------------------------------------ small facts
def aliases_of(rules):
    """Aliases defined in the Left/Right columns, e.g. 'CBS.ACCOUNT (a)' defines a."""
    found = {}
    for r in rules:
        for cell in (r["left"], r["right"]):
            m = re.search(r"\((\w+)\)\s*$", cell.strip())
            if m:
                found[m.group(1)] = cell
            elif re.fullmatch(r"\w+", cell.strip()) and cell.strip() != "-":
                found.setdefault(cell.strip(), cell)
    return found


def parameters_of(model):
    text = " ".join(r["expr"] for r in model["rules"]) + " " + " ".join(m["logic"] for m in model["mappings"])
    return sorted(set(re.findall(r":([A-Za-z_]\w*)", text)))


def table_names(cell):
    """Table names in a Source Table cell. Copes with 'order_line (l), product (p)' and 'x, derived from M02'."""
    names = []
    for part in str(cell).split(","):
        part = re.sub(r"\s*\(.*?\)", "", part).strip()
        if part and part != "-" and re.fullmatch(r"[\w.]+", part) and not part.lower().startswith("derived"):
            names.append(part)
    return names


def join_word(text):
    """'INNER (plain JOIN)' -> 'INNER'. Blank stays blank."""
    m = re.match(r"\s*([A-Za-z]+)", text or "")
    return m.group(1).upper() if m else ""


def source_tables(model):
    names = {}
    for m in model["mappings"]:
        for t in table_names(m["source_table"]):
            key = ".".join(p for p in (m["source_schema"], t) if p and p != "-" and p.upper() != "UNKNOWN")
            names[key] = names.get(key, 0) + 1
    return names


def summary(model):
    m, r = model["mappings"], model["rules"]
    count = lambda t: sum(1 for x in r if x["type"].lower() == t)
    joins = {}
    for x in r:
        if x["type"].lower() == "join":
            word = join_word(x["join_type"]) or "?"
            joins[word] = joins.get(word, 0) + 1
    targets = sorted({".".join(p for p in (x["target_schema"], x["target_table"]) if p) for x in m})
    return {"columns": len(m), "rules": len(r), "target_tables": targets, "source_tables": source_tables(model),
            "joins": joins, "filters": count("filter"), "dedups": count("dedup"), "aggregations": count("aggregation"),
            "parameters": parameters_of(model), "has_reference_sql": bool(model.get("reference_sql"))}


# ------------------------------------------------------------------ check the mapping itself
def lint(model):
    """Checks that need no AI. Returns [{severity, where, message}] with severity high/medium/low/info."""
    out = []
    add = lambda sev, where, msg: out.append({"severity": sev, "where": where, "message": msg})
    mappings, rules = model["mappings"], model["rules"]
    ids = [m["id"] for m in mappings]
    for dup in sorted({i for i in ids if i and ids.count(i) > 1}):
        add("high", dup, "Map ID is used more than once.")
    seen = {}
    for m in mappings:
        where = m["id"] or m["target_column"] or "row"
        if not m["id"]:
            add("low", where, "Row has no Map ID.")
        if not m["target_column"]:
            add("high", where, "No target column.")
            continue
        key = (m["target_table"].lower(), m["target_column"].lower())
        if key in seen:
            add("high", where, "Target column %s is also mapped in %s." % (m["target_column"], seen[key]))
        seen[key] = where
        no_source = m["source_column"] in ("", "-")
        if no_source and not m["logic"]:
            add("high", where, "%s has no source column and no rule, so nothing can fill it." % m["target_column"])
        elif not m["logic"]:
            add("medium", where, "%s has no transformation rule. Assuming a direct move." % m["target_column"])
        if "lookup" in m["logic"].lower() and "default" not in m["logic"].lower():
            add("medium", where, "Lookup has no stated default for unmatched rows.")
    if mappings and not any("pk" in m["key"].lower() for m in mappings):
        add("medium", "mapping", "No column is marked as a primary key (PK).")
    if len({m["target_table"] for m in mappings if m["target_table"]}) > 1:
        add("info", "mapping", "More than one target table. Each will need its own statement.")

    known = set(ids)
    defined = aliases_of(rules)
    schemas = {m["source_schema"].lower() for m in mappings} | {m["target_schema"].lower() for m in mappings} | {"dual"}
    if rules:
        if not any(x["type"].lower() == "driver" for x in rules):
            add("high", "rules", "No Driver (base table) rule, so the main table is not defined.")
        if not any(x["type"].lower() == "target load" for x in rules):
            add("low", "rules", "No Target Load rule (insert, merge or overwrite).")
    seqs = []
    for x in rules:
        where = "Rule %s" % (x["seq"] or "?")
        try:
            seqs.append(int(float(x["seq"])))
        except ValueError:
            add("low", where, "Seq is not a number.")
        if x["type"].lower() == "join":
            if not x["expr"] or x["expr"] == "-":
                add("high", where, "Join has no condition. This would multiply rows.")
            if join_word(x["join_type"]) not in ("INNER", "LEFT", "RIGHT", "FULL", "CROSS"):
                add("medium", where, "Join type is missing or unknown: '%s'." % x["join_type"])
        for i in [a.strip() for a in re.split(r"[,;]", x["applies"]) if a.strip() and a.strip().lower() != "all"]:
            if i not in known:
                add("high", where, "Applies to %s, which is not in the mapping." % i)
        for a in sorted(set(re.findall(r"\b([A-Za-z]\w*)\.\w+", re.sub(r"'[^']*'", "", x["expr"])))):
            if a not in defined and a.lower() not in schemas:
                add("medium", where, "Uses alias '%s' that no rule defines." % a)
    if seqs and seqs != sorted(seqs):
        add("low", "rules", "Seq numbers are not in ascending order.")
    mentioned = " ".join(x["left"] + " " + x["right"] + " " + x["expr"] for x in rules).lower()
    for table in sorted({t for m in mappings for t in table_names(m["source_table"])}):
        if rules and table.split(".")[-1].lower() not in mentioned:
            add("medium", table, "Source table %s is mapped but never used in any join or filter rule." % table)
    params = parameters_of(model)
    if params:
        add("info", "rules", "Run-time parameters needed: " + ", ".join(":" + p for p in params))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(out, key=lambda i: order[i["severity"]])


# ------------------------------------------------------------------ check code against the mapping
SQL_WORDS = {"and", "or", "not", "in", "is", "null", "select", "from", "where", "as", "on", "by", "group", "order", "over",
             "partition", "then", "else", "case", "when", "end", "between", "like", "distinct", "exists", "desc", "asc", "the"}


def _flat(text):
    """Lower-case, drop 'alias.' prefixes, unify operators, collapse spaces."""
    t = re.sub(r"\b[a-z_]\w*\.(?=[a-z_])", "", text.lower())
    t = t.replace("!=", "<>")
    return re.sub(r"\s+", " ", re.sub(r"\s*(<>|>=|<=|=|<|>|\(|\)|,)\s*", r" \1 ", t)).strip()


def _names(expr):
    """Column names and quoted values in an expression (alias prefixes removed)."""
    e = re.sub(r"\b[A-Za-z_]\w*\.(?=[A-Za-z_])", "", expr)
    values = re.findall(r"'[^']*'", e)
    words = [w.lower() for w in re.findall(r"[A-Za-z_]\w*", re.sub(r"'[^']*'", " ", e))]
    return sorted({w for w in words if w not in SQL_WORDS and not w.isdigit()}), [v.lower() for v in values]


def _clean(expr):
    """Drop trailing prose in brackets, e.g. 'GROUP BY ACCT_ID (txn sub-query level)'."""
    expr = re.split(r"\s*,?\s*\b(meaning|i\.e\.|which means)\b", expr, maxsplit=1, flags=re.I)[0]
    def drop(m):
        return m.group(0) if re.search(r"\b(select|from|where|partition|order|over)\b", m.group(1), re.I) else ""
    return re.sub(r"\(([^()=<>',\d]*\s[^()=<>',\d]*)\)\s*$", drop, expr).strip()


def _status(expr, code_flat, code_words, code_text):
    """Returns (status, note). found = exact text present. verify = names present, text differs, or only part of the rule is present. missing = nothing found."""
    pieces = [p.strip() for p in re.split(r"\band\b|\bor\b", _clean(expr), flags=re.I) if p.strip()]
    results = []
    for piece in pieces:
        if _flat(piece) and _flat(piece) in code_flat:
            results.append("found")
            continue
        words, values = _names(piece)
        have = [w for w in words if w in code_words] + [v for v in values if v in code_text.lower()]
        total = len(words) + len(values)
        results.append("verify" if total == 0 or len(have) == total else "missing")
    if all(r == "found" for r in results):
        return "found", ""
    if all(r == "missing" for r in results):
        return "missing", "Key names from this rule are not in the code."
    if "missing" in results:
        gone = "; ".join(p for p, r in zip(pieces, results) if r == "missing")
        return "verify", "Only part of this rule is present. Missing: " + gone
    return "verify", "Names are present but not this exact expression. A person should confirm."


def coverage(model, code):
    """Does the code mention every mapped column, join and filter? Names and text only; it does not run the code."""
    low = code.lower()
    words = set(re.findall(r"[a-z_]\w*", low))
    flat = _flat(code)
    positional = bool(re.search(r"insert\s+(into|overwrite)\s+[\w.]+\s+(select|with)\b", low)) and " as " not in low
    rows = []
    for m in model["mappings"]:
        col = m["target_column"].lower()
        funcs = sorted({f.lower() for f in re.findall(r"\b([A-Z][A-Z_]{2,})\s*\(", m["logic"])} | {"current_timestamp"} & set(re.findall(r"current_timestamp", m["logic"].lower())))
        missing_funcs = [f for f in funcs if f not in low]
        if positional:
            status, note = "unchecked", "INSERT has no column list, so column names cannot be checked."
        elif col in words:
            status, note = ("verify", "Rule mentions %s, not found in the code." % ", ".join(missing_funcs)) if missing_funcs else ("found", "")
        else:
            status, note = "missing", "Target column name does not appear in the code."
        rows.append({"kind": "Column", "ref": m["id"] or m["target_column"], "name": m["target_column"], "status": status, "note": note})
    for x in model["rules"]:
        kind = x["type"].title() or "Rule"
        if kind.lower() in ("driver", "target load"):
            if kind.lower() == "driver":
                names = _names(re.sub(r"\(.*?\)\s*$", "", x["left"]))[0]
                present = [n for n in names if n in words]
                status = "found" if names and len(present) == len(names) else ("missing" if not present else "verify")
                note = "" if status == "found" else "Base table not clearly present."
            else:
                is_merge = "merge" in x["expr"].lower()
                status = "found" if (not is_merge or "merge" in low) else "verify"
                note = "" if status == "found" else "Rule says MERGE; the code does not use MERGE."
            rows.append({"kind": kind, "ref": "Rule " + x["seq"], "name": x["name"], "status": status, "note": note})
            continue
        status, note = _status(x["expr"], flat, words, code)
        if kind.lower() == "aggregation" and "group by" not in low:
            status, note = "missing", "The code has no GROUP BY."
        if kind.lower() == "join":
            table = re.sub(r"\(.*?\)\s*$", "", x["right"]).strip()
            base = re.findall(r"[A-Za-z_]\w*", re.sub(r"\s*\(\w+\)\s*$", "", table).split(".")[-1])[:1] if not table.startswith("(") else _from_tables(table)
            if base and not any(b.lower() in words for b in base):
                status, note = "missing", "Table %s does not appear in the code." % base[0]
        rows.append({"kind": kind, "ref": "Rule " + x["seq"], "name": x["name"], "status": status, "note": note})
    for p in parameters_of(model):
        ok = (":" + p.lower() in low) or p.lower() in words
        rows.append({"kind": "Parameter", "ref": ":" + p, "name": p, "status": "found" if ok else "missing", "note": "" if ok else "Run-time parameter is never used."})
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("found", "verify", "missing", "unchecked")}
    return {"rows": rows, "counts": counts, "total": len(rows), "positional_insert": positional}


def _from_tables(text):
    return [t.split(".")[-1] for t in re.findall(r"\bfrom\s+([\w.]+)", text, flags=re.I)][:1]


# ------------------------------------------------------------------ build the prompt text
def render_model(model):
    """The mapping as plain text for a prompt."""
    lines = ["MAPPING (one line per target column):"]
    for m in model["mappings"]:
        src = ".".join(p for p in (m["source_schema"], m["source_table"], m["source_column"]) if p and p != "-") or "(none)"
        tgt = ".".join(p for p in (m["target_schema"], m["target_table"], m["target_column"]) if p)
        lines.append("%s: %s (%s) -> %s (%s) key=%s nullable=%s | rule: %s%s" % (
            m["id"], src, m["source_type"], tgt, m["target_type"], m["key"] or "-", m["nullable"] or "-", m["logic"] or "direct move",
            (" | note: " + m["notes"]) if m["notes"] else ""))
    lines += ["", "JOINS, FILTERS AND RULES (execute in this order):"]
    for x in model["rules"]:
        lines.append("%s. [%s] %s | left=%s | right=%s | join=%s | expression: %s | why: %s | applies to: %s" % (
            x["seq"], x["type"], x["name"], x["left"] or "-", x["right"] or "-", x["join_type"] or "-", x["expr"], x["logic"], x["applies"] or "-"))
    return "\n".join(lines)


# ------------------------------------------------------------------ read a Copilot reply back into a model
def _md_tables(text):
    lines = [l for l in text.replace("\r", "").split("\n") if not l.strip().startswith("```")]
    tables, i = [], 0
    split = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    while i < len(lines) - 1:
        if lines[i].count("|") >= 2 and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            head, rows, i = split(lines[i]), [], i + 2
            while i < len(lines) and lines[i].count("|") >= 2:
                rows.append(split(lines[i]))
                i += 1
            tables.append((head, rows))
        else:
            i += 1
    return tables


def model_from_reply(text):
    """Turn the two markdown tables in a Copilot reply into the same model that load_xlsx gives."""
    mappings = rules = None
    for head, rows in _md_tables(text):
        norm = [_norm(h) for h in head]
        for field_of, needed in ((_map_field, ("target_column", "source_column")), (_rule_field, ("seq", "expr"))):
            fields = {}
            for j, h in enumerate(norm):
                f = field_of(h)
                if f and f not in fields:
                    fields[f] = j
            if all(n in fields for n in needed):
                items = [{f: (r[j] if j < len(r) else "") for f, j in fields.items()} for r in rows]
                if field_of is _map_field and mappings is None:
                    mappings = items
                elif field_of is _rule_field and rules is None and "target_column" not in fields:
                    rules = items
    if mappings is None:
        raise ValueError("No mapping table found in the reply. It needs two tables: Source_Target_Mapping and Joins_Filters.")
    for m in mappings:
        for f, _ in MAP_HEADERS:
            m.setdefault(f, "")
    rules = rules or []
    for r in rules:
        for f, _ in RULE_HEADERS:
            r.setdefault(f, "")
    return {"mappings": mappings, "rules": rules, "reference_sql": ""}


# ------------------------------------------------------------------ write .xlsx
def _cell(ref, text, style=0):
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return '<c r="%s" t="inlineStr" s="%d"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, text)


def _col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_xml(title, subtitle, headers, rows):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="%s"><cols>' % NS["m"]]
    out.append("".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, 42 if "Logic" in h or "Condition" in h else 22) for i, (_, h) in enumerate(headers)))
    out.append("</cols><sheetData>")
    out.append('<row r="1">%s</row><row r="2">%s</row>' % (_cell("A1", title, 2), _cell("A2", subtitle)))
    out.append('<row r="4">%s</row>' % "".join(_cell("%s4" % _col_letter(i), h, 1) for i, (_, h) in enumerate(headers)))
    for n, row in enumerate(rows, start=5):
        out.append('<row r="%d">%s</row>' % (n, "".join(_cell("%s%d" % (_col_letter(i), n), row.get(f, ""), 3) for i, (f, _) in enumerate(headers))))
    out.append("</sheetData></worksheet>")
    return "".join(out)


STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="%s">'
          '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
          '<font><b/><sz val="14"/><name val="Calibri"/></font></fonts>'
          '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
          '<fill><patternFill patternType="solid"><fgColor rgb="FF0075BE"/></patternFill></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment wrapText="1" vertical="top"/></xf>'
          '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment wrapText="1" vertical="top"/></xf></cellXfs>'
          '</styleSheet>') % NS["m"]


def write_xlsx(model, title="Source to Target Mapping"):
    """Build a two-sheet .xlsx laid out like the example (title rows, header on row 4). Returns bytes."""
    sheets = [(SHEET_NAMES[0], title, "Generated from code. Review before use.", MAP_HEADERS, model["mappings"]),
              (SHEET_NAMES[1], "Joins, Filters & Business Rules", "Order of execution top to bottom.", RULE_HEADERS, model["rules"])]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(sheets)))
                   + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>' % (
            NS["m"], NS["r"].replace("", ""), "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (s[0], i + 1, i + 1) for i, s in enumerate(sheets))))
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(len(sheets)))
                   + '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' % (len(sheets) + 1))
        z.writestr("xl/styles.xml", STYLES)
        for i, (_, t, sub, headers, rows) in enumerate(sheets):
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), _sheet_xml(t, sub, headers, rows))
    return buf.getvalue()
