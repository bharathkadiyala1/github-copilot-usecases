"""Runs the parity checks and writes reports/modernization_report.md."""
import csv
from datetime import datetime

from modern import pipeline
from modern.config import SYSTEMS
from parity.compare import compare_folders
from parity.legacy_runner import RUNNABLE, run_legacy


def check_system(cfg, system):
    """Run the modern pipeline for one legacy system and compare with the golden output."""
    c = cfg.for_system(system)
    golden = c.golden_dir / system
    if not (golden / "summary.csv").exists():
        raise FileNotFoundError("no golden files for %s - run: python modernize.py setup" % system)
    status = pipeline.run(c)
    diffs = compare_folders(golden, c.output_dir / "modern" / system)
    result = {"system": system, "rounding": c.rounding, "format": c.input_format,
              "accounts": status.get("accounts"), "rejects": status.get("rejects"),
              "diffs": {name: d[0] for name, d in diffs.items()},
              "examples": [m for d in diffs.values() for m in d[1]][:5], "legacy_live": "n/a"}
    if system in RUNNABLE:     # also prove the golden files still match the real legacy code
        live = compare_folders(golden, run_legacy(c, system))
        result["legacy_live"] = "match" if sum(d[0] for d in live.values()) == 0 else "DIFFERENT"
    result["passed"] = sum(result["diffs"].values()) == 0 and result["legacy_live"] != "DIFFERENT"
    return result


def prompt_log_rows(cfg, limit=30):
    path = cfg.audit_dir / "prompt_log.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))[-limit:]


def write_report(cfg, results):
    cfg.report_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Modernization report", "", "Generated: %s" % datetime.now().isoformat(timespec="seconds"), "",
             "## Parity: modern pipeline vs legacy output", "",
             "| System | Input | Rounding | Accounts | Rejects | Summary diffs | Reject diffs | Legacy re-run | Result |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append("| %s | %s | %s | %s | %s | %d | %d | %s | %s |" % (
            r["system"], r["format"], r["rounding"], r["accounts"], r["rejects"], r["diffs"]["summary.csv"],
            r["diffs"]["rejects.csv"], r["legacy_live"], "PASS" if r["passed"] else "FAIL"))
    failed = [r for r in results if not r["passed"]]
    if failed:
        lines += ["", "## Mismatches (first examples)", ""]
        for r in failed:
            lines.append("**%s**" % r["system"])
            lines += ["- " + m for m in r["examples"]]
    lines += ["", "## Copilot prompt log (audit trail)", "",
              "| Time | System | Step | Mode | Status | Reviewer | Human decision |", "|---|---|---|---|---|---|---|"]
    for row in prompt_log_rows(cfg):
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (row["time"], row["system"], row["step"], row["mode"],
                                                              row["status"], row["reviewer"], row["human_decision"]))
    lines += ["", "Note: golden outputs for cobol, sas and ssis are SIMULATED (see golden/README.txt)."]
    path = cfg.report_dir / "modernization_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_all_checks(cfg, systems=None):
    return [check_system(cfg, s) for s in (systems or SYSTEMS)]
