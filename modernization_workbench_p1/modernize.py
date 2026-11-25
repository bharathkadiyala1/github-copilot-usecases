"""Legacy Modernization Workbench - one command line for the whole flow.

    python modernize.py setup          make mock data + golden (expected) outputs
    python modernize.py analyze        ask Copilot to explain the legacy code (rules + tech debt)
    python modernize.py convert        ask Copilot for tests + PySpark, then run the modern pipeline
    python modernize.py parity         compare modern output with legacy output (fails on any mismatch)
    python modernize.py report         write reports/modernization_report.md
    python modernize.py all            setup (if needed) + analyze + convert + parity + report
    python modernize.py demo-break     change the rounding on purpose and show the gate catching it
    python modernize.py health         show the last run status

Options:  --system python|sql|cobol|sas|ssis   --all-systems   --fake-copilot
Settings live in the .env file.
"""
import argparse
import sys

from copilot import client, prompts
from modern import pipeline
from modern.config import ROOT, SYSTEMS, Config
from parity import report
from tools import generate_mock_data, make_golden


def build_config(args, extra=None):
    overrides = dict(extra or {})
    if args.system:
        overrides["LEGACY_SYSTEM"] = args.system
    if args.fake_copilot:
        overrides["COPILOT_CMD"] = '"%s" "%s"' % (sys.executable, ROOT / "tools" / "fake_copilot.py")
    return Config(overrides=overrides)


def systems_to_run(cfg, args):
    return SYSTEMS if args.all_systems else [cfg.system]


def do_setup(cfg):
    rows = generate_mock_data.generate(cfg)
    make_golden.make_golden(cfg)
    print("setup done: %d mock rows, golden outputs in %s" % (rows, cfg.golden_dir))


def do_analyze(cfg, systems):
    for system in systems:
        c = cfg.for_system(system)
        for step in ("analyze_rules", "debt_register"):
            status, _ = client.ask(c, step, prompts.build_prompt(step, c))
            print("analyze %-6s %-14s %s" % (system, step, status))


def do_convert(cfg, systems):
    for system in systems:
        c = cfg.for_system(system)
        for step in ("generate_tests", "convert_pyspark"):
            status, _ = client.ask(c, step, prompts.build_prompt(step, c))
            print("convert %-6s %-16s %s" % (system, step, status))
        pipeline.run(c)


def do_parity(cfg, systems):
    results = report.run_all_checks(cfg, systems)
    for r in results:
        print("parity %-6s %s  (summary diffs=%d, reject diffs=%d, legacy re-run=%s)" % (
            r["system"], "PASS" if r["passed"] else "FAIL", r["diffs"]["summary.csv"], r["diffs"]["rejects.csv"], r["legacy_live"]))
        for line in r["examples"][:3]:
            print("      " + line)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Legacy Modernization Workbench")
    parser.add_argument("command", choices=["setup", "analyze", "convert", "parity", "report", "all", "demo-break", "health"])
    parser.add_argument("--system", choices=SYSTEMS)
    parser.add_argument("--all-systems", action="store_true")
    parser.add_argument("--fake-copilot", action="store_true", help="use tools/fake_copilot.py instead of real Copilot")
    args = parser.parse_args(argv)
    cfg = build_config(args)
    systems = systems_to_run(cfg, args)
    needs_data = not cfg.input_fixed.exists() or not cfg.input_csv.exists() or not (cfg.golden_dir / "python" / "summary.csv").exists()

    if args.command == "setup":
        do_setup(cfg)
    elif args.command == "analyze":
        do_analyze(cfg, systems)
    elif args.command == "convert":
        do_convert(cfg, systems)
    elif args.command == "parity":
        results = do_parity(cfg, SYSTEMS if args.all_systems else systems)
        report.write_report(cfg, results)
        return 0 if all(r["passed"] for r in results) else 1
    elif args.command == "report":
        results = report.run_all_checks(cfg, systems)
        print("report written:", report.write_report(cfg, results))
    elif args.command == "all":
        if needs_data:
            do_setup(cfg)
        do_analyze(cfg, systems)
        do_convert(cfg, systems)
        results = do_parity(cfg, systems)
        print("report written:", report.write_report(cfg, results))
        return 0 if all(r["passed"] for r in results) else 1
    elif args.command == "demo-break":
        if needs_data:
            do_setup(cfg)
        broken = build_config(args, {"ROUNDING_OVERRIDE": "ROUND_HALF_EVEN" if cfg.rounding != "ROUND_HALF_EVEN" else "ROUND_DOWN"})
        print("Demo: pretending a developer changed the rounding style to %s ..." % broken.rounding)
        results = do_parity(broken, [broken.system])
        caught = not all(r["passed"] for r in results)
        print("The parity gate %s the mistake." % ("CAUGHT" if caught else "MISSED"))
        return 0 if caught else 1
    elif args.command == "health":
        for system in systems:
            print(pipeline.health(cfg.for_system(system)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
