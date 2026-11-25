"""Compare two versions of a mapping (and two versions of generated code). Pure functions, standard library only."""
import difflib
import re

import mapping

# Which fields matter for the business, which are technical, which are just notes.
BUSINESS_COL = ("logic", "source_schema", "source_table", "source_column", "target_column", "key", "target_schema", "target_table")
TECH_COL = ("source_system", "source_type", "target_type", "nullable")
BUSINESS_RULE = ("type", "left", "right", "join_type", "expr", "applies")
COL_LABEL = dict(mapping.MAP_HEADERS)
RULE_LABEL = dict(mapping.RULE_HEADERS)
RULE_LABEL["expr"] = "Condition / Expression"


def _n(value):
    """Ignore spacing differences so a re-formatted cell is not a change."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _field_changes(old, new, fields, labels):
    return [{"field": labels[f], "key": f, "old": _n(old.get(f)), "new": _n(new.get(f))} for f in fields if _n(old.get(f)) != _n(new.get(f))]


def _kind(changes, business, technical):
    keys = {c["key"] for c in changes}
    if keys & set(business):
        return "business"
    if keys & set(technical):
        return "technical"
    return "notes"


def _col_key(m):
    return ((m.get("target_table") or "").lower(), (m.get("target_column") or "").lower())


def diff_columns(old_rows, new_rows):
    old = {}
    for m in old_rows:
        old.setdefault(_col_key(m), m)
    fields = list(dict(mapping.MAP_HEADERS).keys())
    changed, added, matched = [], [], set()
    leftover_new = []
    for m in new_rows:
        k = _col_key(m)
        if k in old and k not in matched:
            matched.add(k)
            ch = _field_changes(old[k], m, [f for f in fields if f != "id"] + ["id"], COL_LABEL)
            if ch:
                changed.append({"id": m.get("id", ""), "target": m.get("target_column", ""), "changes": ch, "kind": _kind(ch, BUSINESS_COL, TECH_COL)})
        else:
            leftover_new.append(m)
    leftover_old = [m for k, m in old.items() if k not in matched]
    # a column renamed but keeping its Map ID is a change, not an add plus a remove
    for m in list(leftover_new):
        twin = next((o for o in leftover_old if o.get("id") and o.get("id") == m.get("id")), None)
        if twin:
            leftover_new.remove(m)
            leftover_old.remove(twin)
            ch = _field_changes(twin, m, fields, COL_LABEL)
            changed.append({"id": m.get("id", ""), "target": m.get("target_column", ""), "changes": ch, "kind": "business", "renamed_from": twin.get("target_column", "")})
    return {"added": leftover_new, "removed": leftover_old, "changed": changed}


def diff_rules(old_rows, new_rows):
    def key(r, i):
        return (r.get("name") or "").strip().lower() or "seq:" + str(r.get("seq") or i)
    old = {}
    for i, r in enumerate(old_rows):
        old.setdefault(key(r, i), r)
    fields = [f for f, _ in mapping.RULE_HEADERS]
    changed, leftover_new, matched = [], [], set()
    for i, r in enumerate(new_rows):
        k = key(r, i)
        if k in old and k not in matched:
            matched.add(k)
            ch = _field_changes(old[k], r, fields, RULE_LABEL)
            if ch:
                kind = _kind(ch, BUSINESS_RULE, ())
                if kind == "notes" and {c["key"] for c in ch} == {"seq"}:
                    kind = "order"
                changed.append({"seq": r.get("seq", ""), "name": r.get("name", ""), "type": r.get("type", ""), "changes": ch, "kind": kind, "applies": r.get("applies", "")})
        else:
            leftover_new.append(r)
    leftover_old = [r for k, r in old.items() if k not in matched]
    # same Seq but different name = the rule was edited, not replaced
    for r in list(leftover_new):
        twin = next((o for o in leftover_old if _n(o.get("seq")) == _n(r.get("seq")) and _n(o.get("type")) == _n(r.get("type"))), None)
        if twin:
            leftover_new.remove(r)
            leftover_old.remove(twin)
            changed.append({"seq": r.get("seq", ""), "name": r.get("name", ""), "type": r.get("type", ""), "changes": _field_changes(twin, r, fields, RULE_LABEL), "kind": "business", "applies": r.get("applies", "")})
    return {"added": leftover_new, "removed": leftover_old, "changed": changed}


def _affected(rule_applies, model):
    """Target columns named by an 'Applies To Map IDs' cell."""
    ids = [a.strip() for a in re.split(r"[,;]", rule_applies or "") if a.strip()]
    if not ids or any(i.lower() == "all" for i in ids):
        return ["all columns"]
    by_id = {m.get("id"): m.get("target_column") for m in model["mappings"]}
    return [by_id.get(i, i) for i in ids]


def diff_models(old, new):
    cols = diff_columns(old["mappings"], new["mappings"])
    rules = diff_rules(old["rules"], new["rules"])
    for c in rules["changed"]:
        c["affects"] = _affected(c["applies"], new)
    for r in rules["added"]:
        r["affects"] = _affected(r.get("applies"), new)
    for r in rules["removed"]:
        r["affects"] = _affected(r.get("applies"), old)
    p_old, p_new = set(mapping.parameters_of(old)), set(mapping.parameters_of(new))
    counts = {"columns_added": len(cols["added"]), "columns_removed": len(cols["removed"]), "columns_changed": len(cols["changed"]),
              "columns_business": sum(1 for c in cols["changed"] if c["kind"] == "business"),
              "rules_added": len(rules["added"]), "rules_removed": len(rules["removed"]), "rules_changed": len(rules["changed"]),
              "rules_business": sum(1 for c in rules["changed"] if c["kind"] == "business")}
    counts["total"] = sum(counts[k] for k in ("columns_added", "columns_removed", "columns_changed", "rules_added", "rules_removed", "rules_changed"))
    return {"columns": cols, "rules": rules, "parameters": {"added": sorted(p_new - p_old), "removed": sorted(p_old - p_new)}, "counts": counts,
            "summary": summary_text(counts)}


def summary_text(c):
    if c["total"] == 0:
        return "No changes. The two versions are the same apart from spacing."
    parts = []
    if c["columns_changed"] or c["columns_added"] or c["columns_removed"]:
        parts.append("Columns: %d changed (%d business logic), %d added, %d removed" % (c["columns_changed"], c["columns_business"], c["columns_added"], c["columns_removed"]))
    if c["rules_changed"] or c["rules_added"] or c["rules_removed"]:
        parts.append("Rules: %d changed (%d business logic), %d added, %d removed" % (c["rules_changed"], c["rules_business"], c["rules_added"], c["rules_removed"]))
    return ". ".join(parts) + "."


def describe(diff):
    """The changes as plain bullet lines, for a Copilot prompt."""
    out = []
    for c in diff["columns"]["changed"]:
        for ch in c["changes"]:
            out.append("- Column %s (%s): %s changed from '%s' to '%s'" % (c["target"], c["id"] or "-", ch["field"], ch["old"], ch["new"]))
    for m in diff["columns"]["added"]:
        out.append("- NEW column %s (%s): source %s.%s, rule: %s" % (m.get("target_column"), m.get("id") or "-", m.get("source_table"), m.get("source_column"), m.get("logic")))
    for m in diff["columns"]["removed"]:
        out.append("- REMOVED column %s (%s)" % (m.get("target_column"), m.get("id") or "-"))
    for c in diff["rules"]["changed"]:
        for ch in c["changes"]:
            out.append("- Rule %s (%s): %s changed from '%s' to '%s'" % (c["seq"], c["name"], ch["field"], ch["old"], ch["new"]))
    for r in diff["rules"]["added"]:
        out.append("- NEW rule %s (%s, %s): %s" % (r.get("seq"), r.get("type"), r.get("name"), r.get("expr")))
    for r in diff["rules"]["removed"]:
        out.append("- REMOVED rule %s (%s, %s): %s" % (r.get("seq"), r.get("type"), r.get("name"), r.get("expr")))
    for p in diff["parameters"]["added"]:
        out.append("- NEW run-time parameter :%s" % p)
    for p in diff["parameters"]["removed"]:
        out.append("- REMOVED run-time parameter :%s" % p)
    return "\n".join(out) or "- No changes."


# ------------------------------------------------------------------ code diff
def diff_code(old, new, context=2, limit=700):
    raw = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "previous", "current", lineterm="", n=context))
    lines = raw[2:]                       # the first two lines are the ---/+++ file headers; a deleted "-- comment" must not be mistaken for one
    rows, added, removed = [], 0, 0
    for l in lines:
        if l.startswith("@@"):
            kind = "head"
        elif l.startswith("+"):
            kind, added = "add", added + 1
        elif l.startswith("-"):
            kind, removed = "del", removed + 1
        else:
            kind = "same"
        if len(rows) < limit:
            rows.append({"kind": kind, "text": l})
    return {"lines": rows, "added": added, "removed": removed, "truncated": len(lines) > limit, "same": not lines}


def touched(diff, code_diff):
    """For each business-level change, did the code diff touch it? A hint to look, not a verdict."""
    changed_text = " ".join(l["text"][1:] for l in code_diff["lines"] if l["kind"] in ("add", "del")).lower()
    out = []

    def check(label, tokens):
        hit = [t for t in tokens if t and t.lower() in changed_text]
        out.append({"item": label, "status": "touched" if hit else "not_touched", "evidence": hit[:2]})

    for c in diff["columns"]["changed"]:
        if c["kind"] != "notes":
            check("Column " + c["target"], [c["target"], c.get("renamed_from", "")])
    for m in diff["columns"]["added"]:
        check("New column " + m.get("target_column", ""), [m.get("target_column", "")])
    for m in diff["columns"]["removed"]:
        check("Removed column " + m.get("target_column", ""), [m.get("target_column", "")])
    for c in diff["rules"]["changed"]:
        if c["kind"] in ("business", "order"):
            names = mapping._names(" ".join(ch["new"] for ch in c["changes"] if ch["key"] in ("expr", "left", "right")))[0][:4]
            check("Rule %s %s" % (c["seq"], c["name"]), ["rule %s" % c["seq"]] + names)
    for r in diff["rules"]["added"]:
        check("New rule %s %s" % (r.get("seq"), r.get("name")), ["rule %s" % r.get("seq")] + mapping._names(r.get("expr", ""))[0][:4])
    for r in diff["rules"]["removed"]:
        check("Removed rule %s %s" % (r.get("seq"), r.get("name")), ["rule %s" % r.get("seq")] + mapping._names(r.get("expr", ""))[0][:4])
    return out
