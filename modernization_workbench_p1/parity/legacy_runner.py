"""Runs the legacy systems we CAN run here (python and sql) and returns their output folder."""
import subprocess
import sys

from modern.config import ROOT

RUNNABLE = {"python": ROOT / "legacy" / "python" / "legacy_etl.py",
            "sql": ROOT / "legacy" / "sql" / "run_sql_legacy.py"}


def run_legacy(cfg, system):
    out_dir = cfg.output_dir / "legacy" / system
    input_path = cfg.input_fixed if system == "python" else cfg.input_csv
    result = subprocess.run([sys.executable, str(RUNNABLE[system]), str(input_path), str(out_dir)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("legacy %s failed: %s" % (system, result.stderr[-500:]))
    return out_dir
