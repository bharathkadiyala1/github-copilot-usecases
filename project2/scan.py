"""Plain-language facts about a script. Pattern matching only (no AI, no parser), so numbers are approximate.

Works best for SQL, Python and SAS. Other languages get size, hardcoded values and general risk checks.
"""
import re

LANG_BY_EXT = {"sql": "SQL", "py": "Python", "sas": "SAS", "cbl": "COBOL", "cob": "COBOL", "cpy": "COBOL", "sh": "Shell",
               "ksh": "Shell", "xml": "SSIS / XML", "dtsx": "SSIS / XML", "pls": "PL/SQL", "plsql": "PL/SQL", "r": "R", "scala": "Scala", "java": "Java"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def language_of(filename, text):
    """File extension first. With no known extension, recognise the language from its content."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in LANG_BY_EXT:
        return LANG_BY_EXT[ext]
    head = text[:4000]
    low = head.lower()
    first = head.lstrip().splitlines()[0] if head.strip() else ""
    if first.startswith("#!"):
        return "Python" if "python" in first else "Shell"
    if re.search(r"(identification|procedure|data)\s+division", low):
        return "COBOL"
    if re.search(r"^\s*(proc\s+(sql|sort|export|print|means)|%let\b|%macro\b|libname\s)|^\s*data\s+[\w.]+\s*;", low, flags=re.M):
        return "SAS"
    if re.search(r"^\s*import\s+[\w.]+|^\s*from\s+[\w.]+\s+import\b|^\s*def\s+\w+\s*\(", head, flags=re.M):
        return "Python"
    if re.search(r"\b(select|insert into|create table|merge into|update)\b", low):
        return "SQL"
    return "Unknown"


def _strip_comments(text, lang):
    """Return (code_only_text, comment_line_count, blank_line_count, total_lines)."""
    lines = text.splitlines()
    blank = sum(1 for l in lines if not l.strip())
    comments = 0
    if lang in ("SQL", "SAS", "PL/SQL"):
        cleaned = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)
        cleaned = re.sub(r"--[^\n]*", "", cleaned) if lang != "SAS" else cleaned
        cleaned = re.sub(r"^\s*\*[^;\n]*;", "", cleaned, flags=re.M) if lang == "SAS" else cleaned
        for l in lines:
            s = l.strip()
            if s.startswith("--") or s.startswith("/*") or s.startswith("*") or s.endswith("*/"):
                comments += 1
        return cleaned, comments, blank, len(lines)
    if lang in ("Python", "Shell", "R"):
        for l in lines:
            if l.strip().startswith("#"):
                comments += 1
        body = re.sub(r"#[^\n]*", "", text)
        if lang == "Python":
            body = re.sub(r'(\"\"\"|\'\'\').*?\1', lambda m: "\n" * m.group().count("\n"), body, flags=re.S)
        return body, comments, blank, len(lines)
    if lang == "COBOL":
        for l in lines:
            if len(l) > 6 and l[6] == "*":
                comments += 1
        return "\n".join(l for l in lines if not (len(l) > 6 and l[6] == "*")), comments, blank, len(lines)
    return text, 0, blank, len(lines)


def _ident(name):
    return name.strip().strip('"`[]').upper()


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def scan_file(filename, text):
    lang = language_of(filename, text)
    code, comments, blank, total = _strip_comments(text, lang)
    low = code.lower()
    facts = {"file": filename, "language": lang, "lines": total, "blank": blank, "comments": comments,
             "code_lines": max(total - blank - comments, 0)}
    findings, hardcoded = [], []

    def flag(sev, title, why, fix, pattern=None, source=None, limit=3):
        hits = list(re.finditer(pattern, source if source is not None else code, flags=re.I | re.M)) if pattern else [None]
        for h in hits[:limit]:
            findings.append({"severity": sev, "title": title, "why": why, "fix": fix, "file": filename,
                             "line": _line_of(source if source is not None else code, h.start()) if h else None,
                             "snippet": (h.group(0).strip()[:90] if h else "")})
        return len(hits)

    if lang in ("SQL", "SAS", "PL/SQL"):
        read = [m.group(2) for m in re.finditer(r"\b(from|join)\s+([A-Za-z_][\w.\"`\[\]]*)", code, flags=re.I)]
        ctes = {m.group(1).upper() for m in re.finditer(r"(?:\bwith\b|,)\s*([A-Za-z_]\w*)\s+as\s*\(", code, flags=re.I)}
        written = [m.group(2) for m in re.finditer(r"\b(insert\s+into|insert\s+overwrite|update|merge\s+into|delete\s+from)\s+([A-Za-z_][\w.\"`\[\]]*)", code, flags=re.I)]
        created = [m.group(1) for m in re.finditer(r"\bcreate\s+(?:or\s+replace\s+)?(?:temp\w*\s+)?table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_][\w.\"`\[\]]*)", code, flags=re.I)]
        last = [x for x in code.split(";") if x.strip()]
        ends_with_select = bool(last) and bool(re.match(r"\s*(with\b|select\b)", last[-1], flags=re.I)) and not re.search(r"\binto\b|\bcreate\b", last[-1], flags=re.I)
        made = sorted({_ident(t) for t in created})
        read_set = sorted({_ident(t) for t in read} - ctes - {"SELECT", "DUAL"})
        wr_set = sorted({_ident(t) for t in written})
        joins = {}
        for m in re.finditer(r"\b(inner|left|right|full|cross)?\s*(?:outer\s+)?join\b", code, flags=re.I):
            k = (m.group(1) or "inner").upper()
            joins[k] = joins.get(k, 0) + 1
        facts.update(tables_read=read_set, tables_written=wr_set, tables_created=made, ends_with_select=ends_with_select, ctes=sorted(ctes), joins=joins,
                     join_count=sum(joins.values()), case_rules=len(re.findall(r"\bcase\b", low)),
                     subqueries=len(re.findall(r"\(\s*select\b", low)), window_functions=len(re.findall(r"\bover\s*\(", low)),
                     aggregations=len(re.findall(r"\b(sum|count|avg|min|max)\s*\(", low)), group_bys=len(re.findall(r"\bgroup\s+by\b", low)),
                     filters=len(re.findall(r"\bwhere\b", low)), statements=len([s for s in code.split(";") if s.strip()]))
        flag("medium", "SELECT *", "Pulls every column, so adding a column upstream silently changes results.", "List the columns you need.", r"select\s+(distinct\s+)?\*")
        flag("medium", "Comma join in FROM", "Old-style join syntax; a missing WHERE condition becomes a huge cross join.", "Use explicit JOIN ... ON.", r"\bfrom\s+[\w.]+(\s+\w+)?\s*,\s*[\w.]+")
        flag("high", "UPDATE or DELETE without WHERE", "Changes every row in the table.", "Add a WHERE clause or confirm this is intended.", r"\b(update\s+[\w.]+\s+set\s+[^;]*?|delete\s+from\s+[\w.]+)\s*(;|$)")
        flag("medium", "NOT IN with a subquery", "If the subquery returns a NULL, the whole filter returns no rows.", "Use NOT EXISTS or filter out NULLs.", r"\bnot\s+in\s*\(\s*select")
        flag("medium", "Float type for money", "Floating point cannot store cents exactly.", "Use DECIMAL / NUMERIC.", r"\b(float|real|double)\b")
        flag("low", "ROUND used", "Rounding style differs between systems (half up, half even, truncate). A common cause of mismatches.", "Document and test the rounding rule.", r"\bround\s*\(")
        flag("low", "DISTINCT used", "Often hides duplicate rows caused by a join problem.", "Find out why duplicates appear.", r"\bselect\s+distinct\b", limit=2)
        if facts["join_count"] >= 5:
            findings.append({"severity": "low", "title": "Many joins (%d)" % facts["join_count"], "why": "Hard to read and test as one block.", "fix": "Split into steps or CTEs.", "file": filename, "line": None, "snippet": ""})
        if lang == "SQL" and re.search(r"\b(insert|merge)\b", low) and not re.search(r"insert\s+(into|overwrite)\s+[\w.]+\s*\(", low) and re.search(r"insert\s+into\s+[\w.]+\s+select", low):
            findings.append({"severity": "medium", "title": "INSERT without a column list", "why": "Relies on column order. Reordering the target table breaks it silently.", "fix": "Name the target columns.", "file": filename, "line": None, "snippet": ""})
    elif lang == "Python":
        facts.update(imports=sorted({m.group(1).split(".")[0] for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)", code, flags=re.M)}),
                     functions=len(re.findall(r"^\s*def\s+\w+", code, flags=re.M)), classes=len(re.findall(r"^\s*class\s+\w+", code, flags=re.M)),
                     loops=len(re.findall(r"^\s*(for|while)\b", code, flags=re.M)), conditions=len(re.findall(r"^\s*(if|elif)\b", code, flags=re.M)),
                     files_opened=len(re.findall(r"\bopen\s*\(|read_csv|read_excel|to_csv", code)), tables_read=[], tables_written=[], join_count=0)
        flag("medium", "No error handling", "A bad row or missing file stops the job with no useful message.", "Wrap file and parsing steps in try/except and log the reason.", None) if not re.search(r"\btry\s*:", code) and facts["files_opened"] else None
        flag("medium", "Float used for amounts", "Floats cannot store cents exactly; totals can drift.", "Use decimal.Decimal.", r"\bfloat\s*\(")
        flag("low", "round() on a number", "Python's round() uses banker's rounding on floats, a common cause of off-by-a-cent differences.", "Use Decimal with an explicit rounding mode.", r"\bround\s*\(")
        flag("medium", "SQL built from text", "String-built SQL is fragile and can be injected.", "Use bound parameters.", r"(execute|read_sql)\s*\(\s*f?[\"'].*(\+|%s|\{)")
        flag("low", "print used for logging", "No timestamps or levels, hard to monitor.", "Use the logging module.", r"^\s*print\s*\(", limit=1)
        flag("low", "Bare except", "Hides every error, including bugs.", "Catch specific exceptions.", r"except\s*:")
    else:
        facts.update(tables_read=[], tables_written=[], join_count=0)
        if lang == "COBOL":
            facts.update(paragraphs=len(re.findall(r"^\s{7}[A-Z0-9-]+\.\s*$", code, flags=re.M)), performs=len(re.findall(r"\bPERFORM\b", code)),
                         computes=len(re.findall(r"\bCOMPUTE\b", code)), files=len(re.findall(r"\bSELECT\s+[\w-]+\s+ASSIGN\b", code)))
            flag("medium", "COMPUTE without ROUNDED", "COBOL truncates instead of rounding unless ROUNDED is stated.", "Check the intended rounding.", r"\bCOMPUTE\b(?![^.]*\bROUNDED\b)[^.]*\.")

    # checks for every language
    flag("high", "Password or secret in the script", "Credentials in code get copied into repositories and logs.", "Move to a secrets store or environment settings.", r"(password|passwd|pwd|secret|api[_-]?key)\s*[=:]\s*[\"'][^\"']+[\"']", source=text)
    for m in re.finditer(r"[\"']((?:[A-Za-z]:\\|/)[\w./\\ -]{3,}|[\w./\\-]+\.(?:csv|txt|dat|xlsx|parquet))[\"']", text):
        hardcoded.append({"kind": "File path", "value": m.group(1), "line": _line_of(text, m.start())})
    for m in re.finditer(r"(?<![\w.'\"-])(\d+\.\d{2,6})(?![\w.'\"])", code):
        hardcoded.append({"kind": "Rate / decimal", "value": m.group(1), "line": _line_of(code, m.start())})
    for m in re.finditer(r"'(\d{4}-\d{2}-\d{2}|\d{8})'", code):
        hardcoded.append({"kind": "Date", "value": m.group(1), "line": _line_of(code, m.start())})
    for m in re.finditer(r"\b(todo|fixme|hack|xxx)\b", text, flags=re.I):
        hardcoded.append({"kind": "TODO note", "value": text[m.start():m.start() + 50].split("\n")[0], "line": _line_of(text, m.start())})
    if any(h["kind"] == "File path" for h in hardcoded):
        findings.append({"severity": "medium", "title": "Hardcoded file paths", "why": "The job only works on one machine or folder layout.", "fix": "Pass paths as parameters or settings.", "file": filename, "line": hardcoded[[h["kind"] for h in hardcoded].index("File path")]["line"], "snippet": ""})
    if any(h["kind"] == "Rate / decimal" for h in hardcoded):
        findings.append({"severity": "low", "title": "Hardcoded rates or constants", "why": "Business values buried in code are easy to miss when they change.", "fix": "Move to a config table or settings file.", "file": filename, "line": None, "snippet": ", ".join(sorted({h["value"] for h in hardcoded if h["kind"] == "Rate / decimal"})[:5])})
    commented_code = sum(1 for l in text.splitlines() if re.match(r"^\s*(--|#)\s*(select|insert|update|delete|from|join|where|import|def|print)\b", l, flags=re.I))
    if commented_code >= 2:
        findings.append({"severity": "low", "title": "Commented-out code (%d lines)" % commented_code, "why": "Unclear whether it is still needed.", "fix": "Delete it (version control keeps history).", "file": filename, "line": None, "snippet": ""})
    facts["hardcoded"] = hardcoded[:40]
    facts["findings"] = sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])
    return facts


def _complexity(files):
    score = 0
    lines = sum(f["code_lines"] for f in files)
    score += min(lines / 10, 30)
    score += min(sum(f.get("join_count", 0) for f in files) * 3, 20)
    score += min(sum(f.get("case_rules", 0) for f in files) * 2, 15)
    score += min(sum(f.get("subqueries", 0) + f.get("window_functions", 0) for f in files) * 3, 15)
    score += min(sum(f.get("loops", 0) + f.get("conditions", 0) + f.get("functions", 0) for f in files) * 1.5, 20)
    score = int(min(score, 100))
    return score, ("Low" if score < 25 else "Medium" if score < 55 else "High")


def _plain(files, total):
    """A few sentences a non-technical reader can follow."""
    parts = []
    for f in files:
        s = "%s is a %s script of %d lines (%d of them code)." % (f["file"], f["language"], f["lines"], f["code_lines"])
        if f.get("tables_read"):
            s += " It reads %d table%s (%s)." % (len(f["tables_read"]), "" if len(f["tables_read"]) == 1 else "s", ", ".join(f["tables_read"][:6]))
        if f.get("tables_created"):
            s += " It creates %d table%s (%s)." % (len(f["tables_created"]), "" if len(f["tables_created"]) == 1 else "s", ", ".join(f["tables_created"][:5]))
        if f.get("tables_written"):
            s += " It writes data to %d (%s)." % (len(f["tables_written"]), ", ".join(f["tables_written"][:4]))
        if f.get("ends_with_select"):
            s += " It ends with a query that returns rows (it does not save them to a table)."
        if f.get("files_opened"):
            s += " It opens %d file%s." % (f["files_opened"], "" if f["files_opened"] == 1 else "s")
        bits = []
        if f.get("join_count"):
            bits.append("%d join%s" % (f["join_count"], "" if f["join_count"] == 1 else "s"))
        if f.get("case_rules"):
            bits.append("%d CASE rule%s" % (f["case_rules"], "" if f["case_rules"] == 1 else "s"))
        if f.get("aggregations"):
            bits.append("%d total/count calculation%s" % (f["aggregations"], "" if f["aggregations"] == 1 else "s"))
        if f.get("window_functions"):
            bits.append("%d ranking/window step%s" % (f["window_functions"], "" if f["window_functions"] == 1 else "s"))
        if f.get("functions"):
            bits.append("%d function%s" % (f["functions"], "" if f["functions"] == 1 else "s"))
        if f.get("loops"):
            bits.append("%d loop%s" % (f["loops"], "" if f["loops"] == 1 else "s"))
        if bits:
            s += " It uses " + ", ".join(bits) + "."
        parts.append(s)
    high = sum(1 for f in files for x in f["findings"] if x["severity"] == "high")
    med = sum(1 for f in files for x in f["findings"] if x["severity"] == "medium")
    parts.append("Overall complexity is %s (%d out of 100). Found %d high and %d medium things to watch." % (total["complexity"], total["score"], high, med))
    return " ".join(parts)


def scan_sources(sources):
    """sources: {filename: text}. Returns facts per file plus an overall summary."""
    files = [scan_file(n, t) for n, t in sorted(sources.items())]
    score, label = _complexity(files)
    findings = sorted([x for f in files for x in f["findings"]], key=lambda f: SEVERITY_ORDER[f["severity"]])
    total = {"files": len(files), "lines": sum(f["lines"] for f in files), "code_lines": sum(f["code_lines"] for f in files),
             "comments": sum(f["comments"] for f in files), "score": score, "complexity": label,
             "tables_read": sorted({t for f in files for t in f.get("tables_read", [])}),
             "tables_written": sorted({t for f in files for t in f.get("tables_written", [])}),
             "tables_created": sorted({t for f in files for t in f.get("tables_created", [])}),
             "joins": sum(f.get("join_count", 0) for f in files), "rules": sum(f.get("case_rules", 0) for f in files),
             "high": sum(1 for x in findings if x["severity"] == "high"), "medium": sum(1 for x in findings if x["severity"] == "medium"),
             "low": sum(1 for x in findings if x["severity"] == "low")}
    return {"files": files, "total": total, "findings": findings, "summary": _plain(files, total) if files else "No script yet."}


def facts_text(scan):
    """Compact facts for a Copilot prompt, so it explains real numbers."""
    t = scan["total"]
    return ("Lines: %d (code %d). Tables read: %s. Tables written: %s (created in script: %s). Joins: %d. CASE rules: %d. Complexity: %s. Hardcoded values: %s." % (
        t["lines"], t["code_lines"], ", ".join(t["tables_read"]) or "none found", ", ".join(t["tables_written"]) or "none found", ", ".join(t["tables_created"]) or "none", t["joins"], t["rules"], t["complexity"],
        ", ".join(sorted({h["value"] for f in scan["files"] for h in f["hardcoded"] if h["kind"] != "TODO note"})[:12]) or "none found"))
