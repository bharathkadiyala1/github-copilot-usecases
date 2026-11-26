"""Unit tests and documentation for generated code. Standard library only.

  extract_tests / reply_section   pull the pieces out of Copilot's reply
  check_tests                     does the test file parse, and does every mapped column and rule have a real test?
  build_documentation             a Markdown document written by the tool from the mapping, the checks and the reply

The tool cannot prove a test is *right*; it proves that each column and rule has a test that exists, parses and asserts something.
"""
import ast
import re

TEST_MARK = re.compile(r"^#{1,4}\s*unit tests?\b.*$", re.I | re.M)


def split_reply(reply):
    """(code part, tests part). The code is whatever comes before the '## Unit tests' heading, so test code is never mistaken for the code."""
    m = TEST_MARK.search(reply)
    return (reply[:m.start()], reply[m.end():]) if m else (reply, "")


def reply_section(reply, title):
    """Text under a '## <title>' heading of the reply, up to the next '## ' heading. Empty when there is none."""
    m = re.search(r"^#{1,4}\s*%s\b.*$" % re.escape(title), reply, re.I | re.M)
    if not m:
        return ""
    rest = reply[m.end():]
    nxt = re.search(r"^#{1,4}\s+\S", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def extract_tests(reply):
    """The first fenced block under the '## Unit tests' heading, or ''."""
    _, tests = split_reply(reply)
    block = re.search(r"```[A-Za-z]*[^\n]*\n(.*?)```", tests, re.S)
    return block.group(1).strip() if block else ""


# ------------------------------------------------------------------ check the tests
def _mentions(text, kind, ref):
    if kind == "Column":
        return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(ref), text, re.I) is not None
    n = re.escape(str(ref).replace("Rule ", "").strip())
    return re.search(r"rule[\s_\-:#]*0*%s(?![0-9])" % n, text, re.I) is not None


def _has_assertion(fn):
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            t = node.test
            if isinstance(t, ast.Constant) and t.value in (True, 1):
                continue                                              # 'assert True' proves nothing
            return True
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name.startswith("assert") or name in ("raises", "approx"):
                return True
    return False


def _skips(fn, source):
    text = ast.get_source_segment(source, fn) or ""
    return bool(re.search(r"pytest\.skip|mark\.skip|unittest\.skip|self\.skipTest", text))


def check_tests(model, code, target=""):
    """{parses, error, tests, rows, counts}. Rows are the mapped columns and rules with the tests that mention them."""
    out = {"parses": False, "error": "", "tests": 0, "rows": [], "counts": {"tested": 0, "weak": 0, "skipped": 0, "missing": 0}, "total": 0, "problems": []}
    if not (code or "").strip():
        out["error"] = "No test file."
        return out
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        out["error"] = "The test file is not valid Python (line %s: %s)." % (e.lineno, e.msg)
        return out
    out["parses"] = True
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
    out["tests"] = len(funcs)
    info = [(f, ast.get_source_segment(code, f) or f.name, _has_assertion(f), _skips(f, code)) for f in funcs]
    if not funcs:
        out["problems"].append("The file has no test functions (names starting with test).")
    if target == "sql" and not re.search(r"duckdb|sqlite3|execute|cursor", code, re.I):
        out["problems"].append("The tests do not appear to run the SQL.")
    if target == "pyspark" and not re.search(r"build_target|SparkSession", code):
        out["problems"].append("The tests do not appear to call build_target.")
    items = [("Column", m["id"] or m["target_column"], m["target_column"]) for m in model["mappings"]]
    items += [("Rule", "Rule " + (x["seq"] or "?"), x["name"]) for x in model["rules"] if (x["type"] or "").lower() != "target load" or x["expr"]]
    for kind, ref, name in items:
        hits = [(f, src, ok, skip) for f, src, ok, skip in info if _mentions(src, kind, ref)]
        real = [h for h in hits if h[2] and not h[3]]
        if real:
            status, note = "tested", ""
        elif hits and all(h[3] for h in hits):
            status, note = "skipped", "Every test for this item is skipped."
        elif hits:
            status, note = "weak", "A test mentions it but asserts nothing."
        else:
            status, note = "missing", "No test mentions this item."
        out["rows"].append({"kind": kind, "ref": ref, "name": name, "status": status, "note": note, "tests": [h[0].name for h in hits][:4]})
        out["counts"][status] += 1
    out["total"] = len(items)
    return out


# ------------------------------------------------------------------ documentation
def _cell(text):
    return re.sub(r"\s+", " ", str(text or "")).replace("|", "\\|").strip() or "-"


def _table(head, rows):
    if not rows:
        return "_None._\n"
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]) + "\n"


def build_documentation(ctx):
    """ctx: name, version, target, dialect, model, summary, lint, coverage, tests (check_tests result), reply, approved, changes (text), files, when."""
    m, s, name, v = ctx["model"], ctx["summary"], ctx["name"], ctx["version"]
    target = {"sql": "SQL", "pyspark": "PySpark"}.get(ctx.get("target"), "")
    L = ["# %s: mapping and code documentation" % name, "",
         "Version %d%s. %s" % (v, (" - " + target) if target else "", ctx.get("when", "")), "",
         "> Written by the tool from the mapping document and its checks. Sections marked *AI draft* come from Copilot and need a person's review.", "",
         "## 1. Purpose", "",
         "Loads %s from %s." % (", ".join(s["target_tables"]) or "the target table", ", ".join(s["source_tables"]) or "the source tables"), "",
         "| Item | Count |", "|---|---|",
         "| Target columns | %d |" % s["columns"], "| Rules (joins, filters, other) | %d |" % s["rules"], "| Source tables | %d |" % len(s["source_tables"]),
         "| Joins | %d |" % sum(s["joins"].values()), "| Filters | %d |" % s["filters"], "| Aggregations | %d |" % s["aggregations"], "| Dedup rules | %d |" % s["dedups"], ""]
    L += ["## 2. How to run", ""]
    if s["parameters"]:
        L += ["Run-time parameters (bind variables):", ""] + ["- `:%s`" % p for p in s["parameters"]] + [""]
    else:
        L += ["No run-time parameters are used.", ""]
    L += ["Files delivered with this version:", ""] + ["- `%s`" % f for f in ctx.get("files", [])] + [""]
    L += ["Status: %s." % ("**code signed off by a person**" if ctx.get("approved") else "code is a **draft** and is not yet signed off"), ""]
    L += ["## 3. Column mapping", "", _table(["Map ID", "Source", "Target", "Key", "Rule"], [
        [x["id"], ".".join(p for p in (x["source_schema"], x["source_table"], x["source_column"]) if p and p != "-") or "(none)",
         ".".join(p for p in (x["target_schema"], x["target_table"], x["target_column"]) if p), x["key"], x["logic"] or "Direct move"] for x in m["mappings"]])]
    L += ["## 4. Joins, filters and rules (in order of execution)", "", _table(["Seq", "Type", "Rule", "Left", "Right", "Join", "Expression", "Why"], [
        [x["seq"], x["type"], x["name"], x["left"], x["right"], x["join_type"], x["expr"], x["logic"]] for x in m["rules"]])]
    issues = [i for i in ctx["lint"] if i["severity"] in ("high", "medium")]
    L += ["## 5. Checks on the mapping document", "",
          "%d finding(s) of medium or high severity." % len(issues) if issues else "No medium or high severity findings.", ""]
    L += [("- **%s** %s: %s" % (i["severity"], i["where"], i["message"])) for i in issues] + ([""] if issues else [])
    det = m.get("detection")
    if det:
        L += ["## 6. How the mapping file was read", "", {"auto": "All columns were recognised clearly.", "confirmed": "Column meanings were confirmed by a person.",
                                                           "remembered": "Column meanings were reused from an earlier confirmed upload."}.get(det.get("by"), ""), ""]
        L += ["- " + w for w in det.get("warnings", [])] + ([""] if det.get("warnings") else [])
    cov = ctx.get("coverage")
    L += ["## 7. Does the code cover the mapping?", ""]
    if cov:
        c = cov["counts"]
        L += ["%d items checked: %d found, %d to verify, %d missing, %d not checkable." % (cov["total"], c["found"], c["verify"], c["missing"], c["unchecked"]), ""]
        bad = [r for r in cov["rows"] if r["status"] in ("missing", "verify")]
        L += [_table(["Kind", "Item", "Status", "Note"], [[r["kind"], "%s %s" % (r["ref"], r["name"]), r["status"], r["note"]] for r in bad])] if bad else []
    else:
        L += ["No code has been generated yet.", ""]
    L += ["## 8. Unit tests", ""]
    t = ctx.get("tests")
    if t and t["parses"]:
        c = t["counts"]
        L += ["%d test function(s). Of %d mapped columns and rules: %d tested, %d weak, %d skipped, %d without a test." % (t["tests"], t["total"], c["tested"], c["weak"], c["skipped"], c["missing"]), ""]
        L += ["- " + p for p in t["problems"]] + ([""] if t["problems"] else [])
        L += [_table(["Kind", "Item", "Status", "Tests"], [[r["kind"], "%s %s" % (r["ref"], r["name"]), r["status"], ", ".join(r["tests"])] for r in t["rows"]])]
    elif t and t["error"] and t["error"] != "No test file.":
        L += [t["error"], ""]
    else:
        L += ["No test file has been saved for this version.", ""]
    L += ["## 9. Changes since the previous version", "", ctx.get("changes") or "This is the first version.", ""]
    notes = ctx.get("assumptions")
    L += ["## 10. Assumptions and open questions (*AI draft*)", "", notes or "_Copilot listed none, or no reply has been saved._", ""]
    return "\n".join(L)
