"""All the logic (no web code here). Standard library only, Python 3.9+.

Parts:  settings  |  jobs  |  prompts  |  run scripts  |  compare  |  audit log  |  report
"""
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
STEPS = ("analyze", "gen_input", "gen_expected", "convert", "cloud")
UNVERIFIED = "Copilot-predicted (UNVERIFIED)"
MAX_PREDICT_LINES = 200
AUDIT_COLUMNS = ["time", "reviewer", "job", "action", "artifact", "sha256", "decision", "comment"]


class UserError(Exception):
    """A problem the user can fix. The page shows the message."""


# ---------------------------------------------------------------- settings
def load_env(path=None):
    values = {}
    path = Path(path) if path else ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Settings:
    def __init__(self, env=None):
        env = load_env() if env is None else env
        get = lambda key, default: env.get(key, default) if env.get(key, "") != "" else default
        self.host = get("HOST", "127.0.0.1")
        self.port = int(get("PORT", "8000"))
        self.reviewer = get("REVIEWER", "")
        self.python_cmd = get("PYTHON_CMD", sys.executable)
        self.timeout = int(get("RUN_TIMEOUT", "60"))
        self.key_columns = get("KEY_COLUMNS", "")
        self.tolerance = get("TOLERANCE", "0")
        self.max_mismatches = int(get("MAX_MISMATCHES", "50"))
        self.jobs_dir = ROOT / get("JOBS_DIR", "jobs")
        self.target = get("MODERN_TARGET", "python")
        self.copilot_cmd = get("COPILOT_CMD", "")
        self.audit_file = ROOT / "audit" / "audit_log.csv"


# ---------------------------------------------------------------- jobs
def check_name(name):
    if not NAME_RE.match(name or ""):
        raise UserError("Job name: use letters, numbers, - and _ only (max 40).")
    return name


def job_dir(cfg, name):
    path = cfg.jobs_dir / check_name(name)
    if not path.is_dir():
        raise UserError("Job '%s' does not exist." % name)
    return path


def create_job(cfg, name):
    path = cfg.jobs_dir / check_name(name)
    if path.exists():
        raise UserError("Job '%s' already exists." % name)
    for sub in ("legacy", "answers", "output"):
        (path / sub).mkdir(parents=True)
    save_meta(path, {"key_columns": cfg.key_columns, "tolerance": cfg.tolerance,
                     "legacy_cmd": "", "modern_cmd": "{python} new_job.py {input} {output}",
                     "input_origin": "provided", "expected_origin": "provided"})
    return path


def list_jobs(cfg):
    cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
    return sorted(p.name for p in cfg.jobs_dir.iterdir() if p.is_dir() and NAME_RE.match(p.name))


def load_meta(path):
    meta_file = path / "job.json"
    return json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}


def save_meta(path, meta):
    (path / "job.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path, default=""):
    return Path(path).read_text(encoding="utf-8") if Path(path).exists() else default


def head_lines(path, count=8):
    if not Path(path).exists():
        return "(none yet)"
    with open(path, encoding="utf-8", newline="") as handle:
        return "".join(line for _, line in zip(range(count), handle))


def sources(path):
    folder = path / "legacy"
    return sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.exists() else []


def write_file(cfg, name, kind, content, filename=None):
    """Save text the user typed or uploaded. kind: source|input|expected|new_job|answer"""
    path = job_dir(cfg, name)
    meta = load_meta(path)
    if kind == "source":
        if not FILE_RE.match(filename or ""):
            raise UserError("Bad file name.")
        target = path / "legacy" / filename
    elif kind == "answer":
        if filename not in STEPS:
            raise UserError("Unknown step.")
        target = path / "answers" / (filename + ".md")
    elif kind in ("input", "expected", "new_job"):
        target = path / {"input": "input.csv", "expected": "expected.csv", "new_job": "new_job.py"}[kind]
    else:
        raise UserError("Unknown file kind.")
    target.write_text(content, encoding="utf-8", newline="")
    if kind == "input":
        meta["input_origin"] = "provided"
    if kind == "expected":
        meta["expected_origin"] = "provided"
    save_meta(path, meta)
    log_event(cfg, "", name, "file_saved", target.name, sha256_of(target), "", "")
    return target


def job_state(cfg, name):
    path = job_dir(cfg, name)
    meta = load_meta(path)
    new_job = path / "new_job.py"
    return {
        "name": name, "meta": meta, "sources": sources(path),
        "has_input": (path / "input.csv").exists(), "has_expected": (path / "expected.csv").exists(),
        "new_job": read_text(new_job), "code_approved": code_is_approved(cfg, name),
        "answers": {step: read_text(path / "answers" / (step + ".md")) for step in STEPS},
        "has_modern_output": (path / "output" / "modern.csv").exists(),
        "last_check": json.loads(read_text(path / "output" / "last_check.json", "null")),
    }


# ---------------------------------------------------------------- prompts
def build_prompt(cfg, name, step):
    if step not in STEPS:
        raise UserError("Unknown step.")
    path = job_dir(cfg, name)
    template = (ROOT / "prompts" / (step + ".txt")).read_text(encoding="utf-8")
    source_text = "\n\n".join("### FILE: %s\n%s" % (n, read_text(path / "legacy" / n)) for n in sources(path))
    if step in ("analyze", "gen_input", "gen_expected") and not source_text:
        raise UserError("Add the legacy script first (step 1).")
    input_full = ""
    if step == "gen_expected":
        input_full = read_text(path / "input.csv")
        if not input_full.strip():
            raise UserError("Add input data first (step 2).")
        if input_full.count("\n") > MAX_PREDICT_LINES:
            raise UserError("Input is too long for Copilot to trace reliably (max %d lines). Use a smaller sample, or get the real legacy output." % MAX_PREDICT_LINES)
    values = {"{source}": source_text or "(no source given)",
              "{rules}": read_text(path / "answers" / "analyze.md", "(not available)"),
              "{input_head}": head_lines(path / "input.csv"), "{input_full}": input_full,
              "{expected_head}": head_lines(path / "expected.csv"),
              "{python_job}": read_text(path / "new_job.py", "(not written yet)"),
              "{target}": cfg.target}
    for key, value in values.items():
        template = template.replace(key, value)
    log_event(cfg, cfg.reviewer, name, "prompt_built", step, hashlib.sha256(template.encode()).hexdigest(), "", "")
    return template


def ask_copilot(cfg, name, step):
    """Optional command mode: send the prompt to COPILOT_CMD on stdin, save the reply."""
    if not cfg.copilot_cmd:
        raise UserError("COPILOT_CMD is blank in .env. Use copy and paste instead.")
    prompt = build_prompt(cfg, name, step)
    try:
        done = subprocess.run(shlex.split(cfg.copilot_cmd, posix=(os.name != "nt")), input=prompt,
                              capture_output=True, text=True, timeout=cfg.timeout * 5)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UserError("Copilot command failed: %s" % error)
    if done.returncode != 0:
        raise UserError("Copilot command failed: %s" % done.stderr[-300:])
    write_file(cfg, name, "answer", done.stdout, step)
    return done.stdout


# ---------------------------------------------------------------- run scripts
def run_command(cfg, template, cwd, input_path, output_path):
    """Run a command template such as '{python} new_job.py {input} {output}'. Never uses a shell."""
    values = {"python": cfg.python_cmd, "input": str(input_path), "output": str(output_path)}
    try:
        tokens = [token.format(**values) for token in shlex.split(template, posix=(os.name != "nt"))]
    except (KeyError, ValueError) as error:
        raise UserError("Bad command template: %s" % error)
    if not tokens:
        raise UserError("The command is empty.")
    if Path(output_path).exists():
        Path(output_path).unlink()
    try:
        done = subprocess.run(tokens, cwd=str(cwd), capture_output=True, text=True, timeout=cfg.timeout)
    except subprocess.TimeoutExpired:
        raise UserError("Stopped: the script ran longer than %d seconds. Raise RUN_TIMEOUT in .env if that is expected." % cfg.timeout)
    except OSError as error:
        raise UserError("Could not start the command: %s" % error)
    if done.returncode != 0:
        raise UserError("Your script failed. Last output:\n%s" % (done.stderr or done.stdout)[-800:])
    if not Path(output_path).exists():
        raise UserError("The script ran but did not write an output file. It must write to the second argument (the {output} path).")
    return done.stdout[-800:]


def run_legacy(cfg, name):
    path = job_dir(cfg, name)
    meta = load_meta(path)
    if not meta.get("legacy_cmd"):
        raise UserError("Can't run the legacy script here: no command is set. Set one in step 3, or add the legacy output yourself.")
    if not (path / "input.csv").exists():
        raise UserError("Add input data first (step 2).")
    message = run_command(cfg, meta["legacy_cmd"], path, path / "input.csv", path / "expected.csv")
    meta["expected_origin"] = "legacy script run here"
    save_meta(path, meta)
    log_event(cfg, cfg.reviewer, name, "legacy_run", "expected.csv", sha256_of(path / "expected.csv"), "", "")
    return message


def run_modern(cfg, name):
    path = job_dir(cfg, name)
    if not (path / "new_job.py").exists():
        raise UserError("Add the new Python code first (step 4).")
    if not code_is_approved(cfg, name):
        raise UserError("Approve the new code first (step 4). Editing the code removes the approval.")
    if not (path / "input.csv").exists():
        raise UserError("Add input data first (step 2).")
    meta = load_meta(path)
    message = run_command(cfg, meta.get("modern_cmd") or "{python} new_job.py {input} {output}",
                          path, path / "input.csv", path / "output" / "modern.csv")
    log_event(cfg, cfg.reviewer, name, "modern_run", "modern.csv", sha256_of(path / "output" / "modern.csv"), "", "")
    return message


# ---------------------------------------------------------------- compare
def _number(text):
    try:
        return Decimal(text.strip())
    except (InvalidOperation, AttributeError):
        return None


def values_equal(expected, actual, tolerance):
    if expected == actual:
        return True
    if tolerance is not None:
        a, b = _number(expected), _number(actual)
        if a is not None and b is not None:
            return abs(a - b) <= tolerance
    return False


def compare_csv(expected_path, actual_path, key_columns=(), tolerance="0", limit=50):
    """Compare two CSV files. Returns a dict with passed, counts and a list of differences.

    key_columns given -> rows are matched by key (order does not matter, loads files in memory).
    no key columns    -> rows are compared by position, streaming (good for big files).
    """
    tol = Decimal(str(tolerance or "0"))
    diffs, per_column, total = [], {}, [0]

    def add(kind, where, column="", expected="", actual=""):
        total[0] += 1
        if column:
            per_column[column] = per_column.get(column, 0) + 1
        if len(diffs) < limit:
            diffs.append({"type": kind, "where": where, "column": column, "expected": expected, "actual": actual})

    with open(expected_path, encoding="utf-8", newline="") as ef, open(actual_path, encoding="utf-8", newline="") as af:
        e_reader, a_reader = csv.reader(ef), csv.reader(af)
        e_head, a_head = next(e_reader, []), next(a_reader, [])
        result = {"passed": False, "expected_rows": 0, "actual_rows": 0, "diff_count": 0, "diffs": diffs,
                  "columns_diverged": per_column, "header_ok": e_head == a_head}
        if e_head != a_head:
            add("header", "header", "", ",".join(e_head), ",".join(a_head))
            result.update(diff_count=total[0], passed=False)
            return result
        for key in key_columns:
            if key not in e_head:
                raise UserError("Key column '%s' is not in the file header: %s" % (key, ",".join(e_head)))
        if key_columns:
            idx = [e_head.index(k) for k in key_columns]
            e_rows = _keyed(e_reader, idx, add, "expected")
            a_rows = _keyed(a_reader, idx, add, "actual")
            result["expected_rows"], result["actual_rows"] = len(e_rows), len(a_rows)
            for key in sorted(e_rows):
                if key not in a_rows:
                    add("missing_row", "key=" + "|".join(key), "", ",".join(e_rows[key]), "(absent)")
                    continue
                _compare_row(e_head, e_rows[key], a_rows[key], "key=" + "|".join(key), tol, add)
            for key in sorted(a_rows):
                if key not in e_rows:
                    add("extra_row", "key=" + "|".join(key), "", "(absent)", ",".join(a_rows[key]))
        else:
            line = 0
            while True:
                e_row, a_row = next(e_reader, None), next(a_reader, None)
                if e_row is None and a_row is None:
                    break
                line += 1
                result["expected_rows"] += e_row is not None
                result["actual_rows"] += a_row is not None
                if e_row is None:
                    add("extra_row", "row %d" % line, "", "(absent)", ",".join(a_row))
                elif a_row is None:
                    add("missing_row", "row %d" % line, "", ",".join(e_row), "(absent)")
                else:
                    _compare_row(e_head, e_row, a_row, "row %d" % line, tol, add)
    result["diff_count"] = total[0]
    result["passed"] = total[0] == 0
    return result


def _keyed(reader, idx, add, label):
    rows = {}
    for number, row in enumerate(reader, start=1):
        if len(row) <= max(idx):
            add("bad_row", "%s row %d" % (label, number), "", "", ",".join(row))
            continue
        key = tuple(row[i] for i in idx)
        if key in rows:
            add("duplicate_key", "%s key=%s" % (label, "|".join(key)), "", "", "")
        rows[key] = row
    return rows


def _compare_row(header, e_row, a_row, where, tol, add):
    if len(e_row) != len(a_row):
        add("column_count", where, "", ",".join(e_row), ",".join(a_row))
        return
    for column, e_val, a_val in zip(header, e_row, a_row):
        if not values_equal(e_val, a_val, tol):
            add("value", where, column, e_val, a_val)


def preview_rows(path, count=10):
    """First `count` data rows of a CSV: returns (header, rows)."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        return header, [row for _, row in zip(range(count), reader)]


def run_check(cfg, name):
    path = job_dir(cfg, name)
    meta = load_meta(path)
    if not (path / "expected.csv").exists():
        raise UserError("No legacy output yet, so there is nothing to compare against. Add it in step 3.")
    if not (path / "output" / "modern.csv").exists():
        raise UserError("Run the new job first (step 4).")
    keys = [k.strip() for k in (meta.get("key_columns") or "").split(",") if k.strip()]
    try:
        result = compare_csv(path / "expected.csv", path / "output" / "modern.csv", keys, meta.get("tolerance") or "0", cfg.max_mismatches)
    except (ValueError, csv.Error) as error:
        raise UserError("Could not compare the files: %s" % error)
    unverified = str(meta.get("expected_origin", "")).startswith("Copilot-predicted")
    sample = "UNVERIFIED Copilot-predicted output" if unverified else ("sample data" if meta.get("input_origin") == "generated" else "provided data")
    result.update(checked_at=datetime.now().isoformat(timespec="seconds"), data_label=sample, unverified=unverified,
                  input_origin=meta.get("input_origin", ""), expected_origin=meta.get("expected_origin", ""),
                  input_sha=sha256_of(path / "input.csv") if (path / "input.csv").exists() else "",
                  expected_sha=sha256_of(path / "expected.csv"), modern_sha=sha256_of(path / "output" / "modern.csv"),
                  key_columns=keys, tolerance=meta.get("tolerance") or "0")
    header, e_rows = preview_rows(path / "expected.csv")
    result["preview"] = {"header": header, "expected": e_rows, "actual": preview_rows(path / "output" / "modern.csv")[1]}
    (path / "output" / "last_check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    log_event(cfg, cfg.reviewer, name, "parity_check", "modern.csv", result["modern_sha"],
              "PASS" if result["passed"] else "FAIL", "%d differences" % result["diff_count"])
    return result


# ---------------------------------------------------------------- audit log
def log_event(cfg, reviewer, job, action, artifact, sha, decision, comment):
    cfg.audit_file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not cfg.audit_file.exists()
    with open(cfg.audit_file, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(AUDIT_COLUMNS)
        writer.writerow([datetime.now().isoformat(timespec="seconds"), reviewer, job, action, artifact, sha, decision, comment])


def read_audit(cfg, job=None):
    if not cfg.audit_file.exists():
        return []
    with open(cfg.audit_file, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if job is None or r["job"] == job]


def approve(cfg, name, artifact, decision, reviewer, comment):
    """Record a human decision. For new_job the decision is tied to the file's checksum."""
    path = job_dir(cfg, name)
    if decision not in ("approve", "reject"):
        raise UserError("Decision must be approve or reject.")
    if not reviewer.strip():
        raise UserError("Enter your name.")
    if artifact == "new_job":
        if not (path / "new_job.py").exists():
            raise UserError("Add the new Python code first (step 4).")
        sha = sha256_of(path / "new_job.py")
    elif artifact == "results":
        check = path / "output" / "last_check.json"
        if not check.exists():
            raise UserError("Run the parity check before approving results.")
        sha = json.loads(check.read_text(encoding="utf-8"))["modern_sha"]
    else:
        raise UserError("Unknown artifact.")
    log_event(cfg, reviewer.strip(), name, "review", artifact, sha, decision, comment)


def code_is_approved(cfg, name):
    path = cfg.jobs_dir / name
    if not (path / "new_job.py").exists():
        return False
    current = sha256_of(path / "new_job.py")
    latest = None
    for row in read_audit(cfg, name):
        if row["action"] == "review" and row["artifact"] == "new_job" and row["sha256"] == current:
            latest = row["decision"]
    return latest == "approve"


# ---------------------------------------------------------------- report
def build_report(cfg, name):
    state = job_state(cfg, name)
    check = state["last_check"]
    out = ["# Parity report: %s" % name, "", "Generated %s" % datetime.now().isoformat(timespec="seconds"), ""]
    if not check:
        out += ["**No parity check has been run yet.**", ""]
    else:
        verdict = "PASS" if check["passed"] else "FAIL"
        out += ["## Result: %s (on %s)" % (verdict, check["data_label"]), "",
                "- Expected rows: %d, modern rows: %d, differences: %d" % (check["expected_rows"], check["actual_rows"], check["diff_count"]),
                "- Compare mode: %s, tolerance: %s" % ("by key " + ",".join(check["key_columns"]) if check["key_columns"] else "row by row", check["tolerance"]),
                "- Input origin: %s. Legacy output origin: %s." % (check["input_origin"], check["expected_origin"]),
                "- SHA-256 input `%s`, legacy output `%s`, modern output `%s`" % (check["input_sha"][:12], check["expected_sha"][:12], check["modern_sha"][:12]), ""]
        if check.get("unverified"):
            out += ["> **UNVERIFIED.** The legacy output was predicted by Copilot, not produced by the real legacy system. This is not proof of parity.", ""]
        elif check["data_label"] == "sample data":
            out += ["> Passed on generated sample data only. Real data may expose cases the sample missed.", ""]
        if check["diffs"]:
            out += ["## Differences (first %d)" % len(check["diffs"]), "", "| Type | Where | Column | Expected | Actual |", "|---|---|---|---|---|"]
            out += ["| %s | %s | %s | %s | %s |" % (d["type"], d["where"], d["column"], d["expected"].replace("|", "/"), d["actual"].replace("|", "/")) for d in check["diffs"]]
            out.append("")
        if check["columns_diverged"]:
            out += ["Columns that differ: " + ", ".join("%s (%d)" % kv for kv in sorted(check["columns_diverged"].items())), ""]
    for step, title in (("analyze", "Business rules and technical debt (Copilot, reviewed by a human)"), ("cloud", "Cloud target skeleton (Copilot, not run)")):
        if state["answers"][step].strip():
            out += ["## " + title, "", state["answers"][step].strip(), ""]
    out += ["## Audit trail", "", "| Time | Reviewer | Action | Artifact | Decision | SHA-256 | Comment |", "|---|---|---|---|---|---|---|"]
    for r in read_audit(cfg, name):
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (r["time"], r["reviewer"], r["action"], r["artifact"], r["decision"], r["sha256"][:12], r["comment"].replace("|", "/")))
    return "\n".join(out) + "\n"
