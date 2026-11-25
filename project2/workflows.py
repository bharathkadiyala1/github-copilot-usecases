"""Job-level actions for the Understand, Mapping-to-code and Code-to-mapping pages.

Each job folder gets:  mapping/mapping.xlsx + model.json   (uploaded mapping document)
                       mapping/derived.json                (mapping rebuilt from code by Copilot, reviewed by a person)
                       output/generated.sql, generated_pyspark.py
"""
import base64
import csv
import hashlib
import io
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import core
import mapdiff
import mapping
import scan

MAX_UPLOAD = 10 * 1024 * 1024
NEW_STEPS = ("explain", "mapping_sql", "mapping_pyspark", "code_to_mapping")
GENERATED = {"sql": "generated.sql", "pyspark": "generated_pyspark.py"}


# ---------------------------------------------------------------- understand
def source_texts(cfg, name):
    path = core.job_dir(cfg, name)
    return {n: core.read_text(path / "legacy" / n) for n in core.sources(path)}


def scan_job(cfg, name, log=True):
    texts = source_texts(cfg, name)
    if not texts:
        raise core.UserError("Add the legacy script first.")
    result = scan.scan_sources(texts)
    if log:
        core.log_event(cfg, cfg.reviewer, name, "script_scanned", "%d file(s)" % len(texts), "", "", "")
    return result


# ---------------------------------------------------------------- prompts for the new steps
def prompt_values(cfg, name, step, options):
    path = core.job_dir(cfg, name)
    if step in ("explain", "code_to_mapping"):
        texts = source_texts(cfg, name)
        if not texts:
            raise core.UserError("Add the legacy script first.")
        found = scan.scan_sources(texts)
        return {"{source}": "\n\n".join("### FILE: %s\n%s" % (n, t) for n, t in texts.items()), "{facts}": scan.facts_text(found)}
    model = load_model(path / "mapping" / "model.json")
    if not model:
        raise core.UserError("Upload the mapping document first.")
    target = "sql" if step == "mapping_sql" else "pyspark"
    return {"{mapping}": mapping.render_model(model), "{dialect}": (options.get("dialect") or cfg.dialect)[:40], "{update_block}": update_block(cfg, name, target, options)}


# ---------------------------------------------------------------- mapping document
def load_model(path):
    return json.loads(core.read_text(path, "null"))


# ---------------------------------------------------------------- versions
def versions_root(path):
    return path / "mapping" / "versions"


def read_versions(path):
    """Metadata of every saved version, oldest first."""
    root = versions_root(path)
    found = []
    if root.exists():
        for d in root.iterdir():
            meta = json.loads(core.read_text(d / "meta.json", "null") or "null")
            if meta:
                found.append(meta)
    return sorted(found, key=lambda m: m["n"])


def _write_version(path, n, filename, data, model, by, note, when=None):
    folder = versions_root(path) / ("v%d" % n)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mapping.xlsx").write_bytes(data)
    (folder / "model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    meta = {"n": n, "filename": filename, "sha": hashlib.sha256(data).hexdigest(), "by": by, "note": note,
            "uploaded": when or datetime.now().isoformat(timespec="seconds")}
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _ensure_first_version(path):
    """Jobs made before versions existed: treat the current mapping as v1."""
    current = path / "mapping" / "model.json"
    if current.exists() and not read_versions(path):
        model = load_model(current)
        data = (path / "mapping" / "mapping.xlsx").read_bytes()
        when = datetime.fromtimestamp(current.stat().st_mtime).isoformat(timespec="seconds")
        _write_version(path, 1, model.get("filename", "mapping.xlsx"), data, model, "", "", when)


def version_dir(path, n):
    return versions_root(path) / ("v%d" % n)


def version_model(path, n):
    return load_model(version_dir(path, n) / "model.json")


def version_code(path, n, target, latest):
    """Generated code of one version (the newest version's code also lives in output/)."""
    code = core.read_text(version_dir(path, n) / GENERATED[target])
    if not code.strip() and n == latest:
        code = core.read_text(path / "output" / GENERATED[target])
    return code


def _archive_current_work(path, latest):
    """Before a new version starts: keep the current code and replies with the version they belong to, then clear them."""
    folder = version_dir(path, latest)
    for target, fname in GENERATED.items():
        src = path / "output" / fname
        if src.exists():
            if not (folder / fname).exists():
                shutil.copy2(src, folder / fname)
            src.unlink()
        reply = path / "answers" / ("mapping_%s.md" % target)
        if reply.exists():
            shutil.copy2(reply, folder / ("reply_%s.md" % target))
            reply.unlink()


def save_mapping_upload(cfg, name, filename, b64, note=""):
    path = core.job_dir(cfg, name)
    try:
        data = base64.b64decode(b64 or "", validate=False)
    except ValueError:
        raise core.UserError("The file could not be read. Try uploading it again.")
    if not data or len(data) > MAX_UPLOAD:
        raise core.UserError("Choose an .xlsx file under 10 MB.")
    if not str(filename).lower().endswith(".xlsx"):
        raise core.UserError("Only .xlsx mapping documents are supported. In Excel use Save As > Excel Workbook (.xlsx).")
    try:
        model = mapping.load_xlsx(data)
    except (ValueError, KeyError) as error:
        raise core.UserError(str(error))
    model["filename"] = re.sub(r"[^A-Za-z0-9_. -]", "_", str(filename))[:80]
    (path / "mapping").mkdir(exist_ok=True)
    _ensure_first_version(path)
    versions = read_versions(path)
    same = versions and versions[-1]["sha"] == hashlib.sha256(data).hexdigest()
    if same:
        state = mapping_state(cfg, name)
        state["upload_result"] = {"new": False, "n": versions[-1]["n"], "message": "This is the same file as version %d, so no new version was created." % versions[-1]["n"]}
        return state
    n = versions[-1]["n"] + 1 if versions else 1
    if versions:
        _archive_current_work(path, versions[-1]["n"])
    _write_version(path, n, model["filename"], data, model, cfg.reviewer, str(note or "")[:300])
    (path / "mapping" / "mapping.xlsx").write_bytes(data)
    (path / "mapping" / "model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    message = "Saved as version %d." % n
    if versions:
        d = mapdiff.diff_models(version_model(path, n - 1), model)
        message += " " + d["summary"]
    core.log_event(cfg, cfg.reviewer, name, "mapping_uploaded", "v%d %s" % (n, model["filename"]), hashlib.sha256(data).hexdigest(), "",
                   "%d columns, %d rules%s" % (len(model["mappings"]), len(model["rules"]), (". " + str(note)[:120]) if note else ""))
    state = mapping_state(cfg, name)
    state["upload_result"] = {"new": True, "n": n, "message": message}
    return state


def _decision_for_sha(cfg, name, artifact, sha):
    latest = None
    for row in core.read_audit(cfg, name):
        if row["action"] == "review" and row["artifact"] == artifact and row["sha256"] == sha:
            latest = row["decision"]
    return latest


def mapping_state(cfg, name):
    path = core.job_dir(cfg, name)
    _ensure_first_version(path)
    model = load_model(path / "mapping" / "model.json")
    if not model:
        return {"model": None}
    versions = read_versions(path)
    latest = versions[-1]["n"] if versions else 1
    out = {"model": model, "summary": mapping.summary(model), "lint": mapping.lint(model), "generated": {}, "version": latest, "diff": None}
    listing = []
    for i, v in enumerate(versions):
        item = dict(v)
        item["summary"] = "First version" if i == 0 else mapdiff.diff_models(version_model(path, versions[i - 1]["n"]), version_model(path, v["n"]))["summary"]
        item["changes"] = 0 if i == 0 else mapdiff.diff_models(version_model(path, versions[i - 1]["n"]), version_model(path, v["n"]))["counts"]["total"]
        item["code"] = {t: bool(version_code(path, v["n"], t, latest).strip()) for t in GENERATED}
        item["approved"] = {t: _decision_for_sha(cfg, name, "generated_" + t, core.sha256_of(version_dir(path, v["n"]) / GENERATED[t])) == "approve"
                            if (version_dir(path, v["n"]) / GENERATED[t]).exists() else False for t in GENERATED}
        listing.append(item)
    out["versions"] = listing
    if len(versions) > 1:
        out["diff"] = mapdiff.diff_models(version_model(path, versions[-2]["n"]), version_model(path, latest))
    out["previous_code"] = {t: bool(len(versions) > 1 and version_code(path, versions[-2]["n"], t, latest).strip()) for t in GENERATED}
    if model.get("reference_sql"):
        out["reference_coverage"] = mapping.coverage(model, model["reference_sql"])
    for target, fname in GENERATED.items():
        code = core.read_text(path / "output" / fname)
        if code.strip():
            out["generated"][target] = {"code": code, "coverage": mapping.coverage(model, code), "approved": artifact_decision(cfg, name, "generated_" + target) == "approve"}
    out["answers"] = {s: core.read_text(path / "answers" / (s + ".md")) for s in ("mapping_sql", "mapping_pyspark")}
    return out


def compare_versions(cfg, name, a, b):
    """What changed in the mapping between versions a and b, and in the generated code."""
    path = core.job_dir(cfg, name)
    versions = read_versions(path)
    have = {v["n"] for v in versions}
    try:
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        raise core.UserError("Choose two versions to compare.")
    if a not in have or b not in have:
        raise core.UserError("Unknown version.")
    latest = versions[-1]["n"]
    diff = mapdiff.diff_models(version_model(path, a), version_model(path, b))
    code = {}
    for target in GENERATED:
        old, new = version_code(path, a, target, latest), version_code(path, b, target, latest)
        if old.strip() and new.strip():
            cd = mapdiff.diff_code(old, new)
            code[target] = {"diff": cd, "touched": mapdiff.touched(diff, cd)}
    return {"from": a, "to": b, "diff": diff, "code": code}


def update_block(cfg, name, target, options):
    """Extra prompt text asking Copilot to UPDATE the previous version's code instead of starting again."""
    if str(options.get("update", "")) != "1":
        return ""
    path = core.job_dir(cfg, name)
    versions = read_versions(path)
    if len(versions) < 2 or target not in GENERATED:
        return ""
    latest, prev = versions[-1]["n"], versions[-2]["n"]
    previous_code = version_code(path, prev, target, latest)
    if not previous_code.strip():
        return ""
    diff = mapdiff.diff_models(version_model(path, prev), version_model(path, latest))
    return ("UPDATE INSTRUCTIONS\nThis is version %d of the mapping. The code below was written for version %d. Update it so it matches the new mapping.\n"
            "- Change only what the listed changes require. Keep everything else exactly as it is (same aliases, comments, layout, order).\n"
            "- Add a comment saying 'changed in v%d' next to every line you change.\n- Reply with the FULL updated code (not a diff), in the same reply shape as below.\n\n"
            "CHANGES SINCE VERSION %d:\n%s\n\nPREVIOUS CODE (version %d):\n```\n%s\n```\n\n" % (latest, prev, latest, prev, mapdiff.describe(diff), prev, previous_code.strip()))


def extract_code(reply, target):
    """First fenced code block of the reply (prefers one tagged for the target language)."""
    blocks = re.findall(r"```([A-Za-z]*)[^\n]*\n(.*?)```", reply, flags=re.S)
    if not blocks:
        return reply.strip()
    want = ("sql",) if target == "sql" else ("python", "pyspark", "py")
    for lang, body in blocks:
        if lang.lower() in want:
            return body.strip()
    return blocks[0][1].strip()


def save_generated(cfg, name, target):
    if target not in GENERATED:
        raise core.UserError("Choose SQL or PySpark.")
    path = core.job_dir(cfg, name)
    reply = core.read_text(path / "answers" / ("mapping_" + target + ".md"))
    if not reply.strip():
        raise core.UserError("Save Copilot's reply first.")
    code = extract_code(reply, target)
    if not code:
        raise core.UserError("No code found in the reply.")
    (path / "output").mkdir(exist_ok=True)
    target_file = path / "output" / GENERATED[target]
    target_file.write_text(code + "\n", encoding="utf-8")
    _ensure_first_version(path)
    versions = read_versions(path)
    if versions:                                             # keep a copy with the version this code is for
        folder = version_dir(path, versions[-1]["n"])
        shutil.copy2(target_file, folder / GENERATED[target])
        (folder / ("reply_%s.md" % target)).write_text(reply, encoding="utf-8")
    core.log_event(cfg, cfg.reviewer, name, "code_extracted", "v%d %s" % (versions[-1]["n"] if versions else 1, GENERATED[target]), core.sha256_of(target_file), "", "")
    return mapping_state(cfg, name)


# ---------------------------------------------------------------- code -> mapping (with versions)
def derived_root(path):
    return path / "mapping" / "derived_versions"


def read_derived_versions(path):
    root = derived_root(path)
    found = []
    if root.exists():
        for d in root.iterdir():
            meta = json.loads(core.read_text(d / "meta.json", "null") or "null")
            if meta:
                found.append(meta)
    return sorted(found, key=lambda m: m["n"])


def derived_dir(path, n):
    return derived_root(path) / ("v%d" % n)


def _script_sha(sources):
    digest = hashlib.sha256()
    for fname in sorted(sources):
        digest.update(fname.encode() + b"\0" + sources[fname].encode() + b"\0")
    return digest.hexdigest()


def _write_derived_version(path, n, model, reply, sources, by, note, when=None):
    folder = derived_dir(path, n)
    (folder / "script").mkdir(parents=True, exist_ok=True)
    (folder / "model.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    (folder / "reply.md").write_text(reply, encoding="utf-8")
    for fname, text in sources.items():
        (folder / "script" / fname).write_text(text, encoding="utf-8", newline="")
    meta = {"n": n, "by": by, "note": note, "uploaded": when or datetime.now().isoformat(timespec="seconds"),
            "script_sha": _script_sha(sources), "files": sorted(sources)}
    (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _ensure_first_derived_version(cfg, path, name):
    current = path / "mapping" / "derived.json"
    if current.exists() and not read_derived_versions(path):
        when = datetime.fromtimestamp(current.stat().st_mtime).isoformat(timespec="seconds")
        _write_derived_version(path, 1, load_model(current), core.read_text(path / "answers" / "code_to_mapping.md"), source_texts(cfg, name), "", "", when)


def derived_model(path, n):
    return load_model(derived_dir(path, n) / "model.json")


def derived_script(path, n):
    folder = derived_dir(path, n) / "script"
    return {p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()} if folder.exists() else {}


def script_changes(path, a, b):
    """Per file: what changed in the script between derived versions a and b."""
    old, new = derived_script(path, a), derived_script(path, b)
    out = {}
    for fname in sorted(set(old) | set(new)):
        if fname not in old:
            out[fname] = {"status": "added", "diff": mapdiff.diff_code("", new[fname])}
        elif fname not in new:
            out[fname] = {"status": "removed", "diff": mapdiff.diff_code(old[fname], "")}
        else:
            cd = mapdiff.diff_code(old[fname], new[fname])
            out[fname] = {"status": "same" if cd["same"] else "changed", "diff": cd}
    return out


def _script_summary(changes):
    changed = [f for f, c in changes.items() if c["status"] != "same"]
    if not changed:
        return "Code unchanged."
    plus = sum(changes[f]["diff"]["added"] for f in changed)
    minus = sum(changes[f]["diff"]["removed"] for f in changed)
    return "Code changed in %d file%s (+%d / -%d lines)." % (len(changed), "" if len(changed) == 1 else "s", plus, minus)


def derived_state(cfg, name):
    path = core.job_dir(cfg, name)
    _ensure_first_derived_version(cfg, path, name)
    model = load_model(path / "mapping" / "derived.json")
    out = {"model": model, "answer": core.read_text(path / "answers" / "code_to_mapping.md"), "approved": artifact_decision(cfg, name, "derived_mapping") == "approve"}
    if not model:
        return out
    texts = "\n".join(source_texts(cfg, name).values())
    out.update(summary=mapping.summary(model), lint=mapping.lint(model), coverage=mapping.coverage(model, texts) if texts else None)
    versions = read_derived_versions(path)
    listing = []
    for i, v in enumerate(versions):
        item = dict(v)
        if i == 0:
            item["summary"], item["changes"] = "First version", 0
        else:
            prev = versions[i - 1]["n"]
            d = mapdiff.diff_models(derived_model(path, prev), derived_model(path, v["n"]))
            item["summary"] = d["summary"] + " " + _script_summary(script_changes(path, prev, v["n"]))
            item["changes"] = d["counts"]["total"]
        item["approved"] = _decision_for_sha(cfg, name, "derived_mapping", core.sha256_of(derived_dir(path, v["n"]) / "model.json")) == "approve"
        listing.append(item)
    out["versions"] = listing
    out["version"] = versions[-1]["n"] if versions else 1
    if len(versions) > 1:
        out["diff"] = mapdiff.diff_models(derived_model(path, versions[-2]["n"]), model)
    return out


def derived_compare(cfg, name, a, b):
    path = core.job_dir(cfg, name)
    have = {v["n"] for v in read_derived_versions(path)}
    try:
        a, b = int(a), int(b)
    except (TypeError, ValueError):
        raise core.UserError("Choose two versions to compare.")
    if a not in have or b not in have:
        raise core.UserError("Unknown version.")
    return {"from": a, "to": b, "diff": mapdiff.diff_models(derived_model(path, a), derived_model(path, b)), "scripts": script_changes(path, a, b)}


def parse_reply(cfg, name, note=""):
    path = core.job_dir(cfg, name)
    reply = core.read_text(path / "answers" / "code_to_mapping.md")
    if not reply.strip():
        raise core.UserError("Save Copilot's reply first.")
    try:
        model = mapping.model_from_reply(reply)
    except ValueError as error:
        raise core.UserError(str(error))
    (path / "mapping").mkdir(exist_ok=True)
    _ensure_first_derived_version(cfg, path, name)
    versions = read_derived_versions(path)
    sources = source_texts(cfg, name)
    if versions:
        latest = versions[-1]
        same_mapping = mapdiff.diff_models(derived_model(path, latest["n"]), model)["counts"]["total"] == 0
        if same_mapping and latest["script_sha"] == _script_sha(sources):
            state = derived_state(cfg, name)
            state["upload_result"] = {"new": False, "n": latest["n"], "message": "Nothing changed since version %d (same mapping, same code), so no new version was created." % latest["n"]}
            return state
    n = versions[-1]["n"] + 1 if versions else 1
    _write_derived_version(path, n, model, reply, sources, cfg.reviewer, str(note or "")[:300])
    derived = path / "mapping" / "derived.json"
    derived.write_text(json.dumps(model, indent=2), encoding="utf-8")
    message = "Saved as version %d." % n
    if versions:
        d = mapdiff.diff_models(derived_model(path, n - 1), model)
        message += " Mapping: " + ("no changes. " if d["counts"]["total"] == 0 else d["summary"] + " ") + _script_summary(script_changes(path, n - 1, n))
    core.log_event(cfg, cfg.reviewer, name, "mapping_parsed", "v%d derived" % n, core.sha256_of(derived), "",
                   "%d columns, %d rules%s" % (len(model["mappings"]), len(model["rules"]), (". " + str(note)[:120]) if note else ""))
    state = derived_state(cfg, name)
    state["upload_result"] = {"new": True, "n": n, "message": message}
    return state


# ---------------------------------------------------------------- approvals and downloads
def artifact_file(cfg, name, artifact):
    path = core.job_dir(cfg, name)
    files = {"explanation": path / "answers" / "explain.md", "derived_mapping": path / "mapping" / "derived.json",
             "generated_sql": path / "output" / GENERATED["sql"], "generated_pyspark": path / "output" / GENERATED["pyspark"]}
    return files.get(artifact)


def artifact_decision(cfg, name, artifact):
    target = artifact_file(cfg, name, artifact)
    if not target or not target.exists():
        return None
    sha, latest = core.sha256_of(target), None
    for row in core.read_audit(cfg, name):
        if row["action"] == "review" and row["artifact"] == artifact and row["sha256"] == sha:
            latest = row["decision"]
    return latest


def _csv_bytes(rows, headers):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([h for _, h in headers])
    for r in rows:
        writer.writerow([r.get(f, "") for f, _ in headers])
    return buf.getvalue().encode("utf-8")


def download(cfg, name, what, version=None):
    """Returns (filename, content type, bytes)."""
    path = core.job_dir(cfg, name)
    if what in ("sql", "pyspark"):
        if version:
            try:
                n = int(version)
            except ValueError:
                raise core.UserError("Unknown version.")
            versions = read_versions(path)
            code = version_code(path, n, what, versions[-1]["n"] if versions else n)
            if not code.strip():
                raise core.UserError("No generated code for that version.")
            return "%s_v%d_%s" % (name, n, GENERATED[what]), "text/plain; charset=utf-8", code.encode("utf-8")
        target = path / "output" / GENERATED[what]
        if not target.exists():
            raise core.UserError("No generated code yet.")
        return name + "_" + GENERATED[what], "text/plain; charset=utf-8", target.read_bytes()
    derived = load_model(path / "mapping" / "derived.json")
    label = ""
    if version and what.startswith("mapping") or version and what == "rules_csv":
        try:
            n = int(version)
        except ValueError:
            raise core.UserError("Unknown version.")
        if n not in {v["n"] for v in read_derived_versions(path)}:
            raise core.UserError("Unknown version.")
        derived, label = derived_model(path, n), "_v%d" % n
    if what.startswith("mapping") and not derived:
        raise core.UserError("Build the mapping from code first.")
    if what == "mapping_xlsx":
        core.log_event(cfg, cfg.reviewer, name, "download", "mapping%s.xlsx" % label, core.sha256_of(path / "mapping" / "derived.json"), "", "")
        return name + "_mapping%s.xlsx" % label, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", mapping.write_xlsx(derived, "Source to Target Mapping - " + name)
    if what == "mapping_csv":
        return name + "_mapping%s.csv" % label, "text/csv; charset=utf-8", _csv_bytes(derived["mappings"], mapping.MAP_HEADERS)
    if what == "rules_csv":
        return name + "_joins_filters%s.csv" % label, "text/csv; charset=utf-8", _csv_bytes(derived["rules"], mapping.RULE_HEADERS)
    raise core.UserError("Unknown download.")
