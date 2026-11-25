"""Builds the golden (expected) outputs in golden/<system>/.

python + sql : captured by really running the legacy code.
cobol/sas/ssis: SIMULATED with tools/simulate_legacy.py (we cannot run them here).
"""
import shutil

from parity.legacy_runner import RUNNABLE, run_legacy
from tools.simulate_legacy import simulate

SIMULATED_MODE = {"cobol": "ROUND_DOWN", "sas": "ROUND_HALF_UP", "ssis": "ROUND_HALF_EVEN"}


def make_golden(cfg):
    for system in RUNNABLE:
        out = run_legacy(cfg, system)
        target = cfg.golden_dir / system
        target.mkdir(parents=True, exist_ok=True)
        for name in ("summary.csv", "rejects.csv"):
            shutil.copy(out / name, target / name)
    for system, mode in SIMULATED_MODE.items():
        summary, rejects = simulate(cfg.input_fixed, mode)
        target = cfg.golden_dir / system
        target.mkdir(parents=True, exist_ok=True)
        (target / "summary.csv").write_text(summary, encoding="utf-8", newline="")
        (target / "rejects.csv").write_text(rejects, encoding="utf-8", newline="")
    (cfg.golden_dir / "README.txt").write_text(
        "golden/python and golden/sql were captured by running the legacy code.\n"
        "golden/cobol, golden/sas and golden/ssis are SIMULATED (those systems cannot run here).\n"
        "In real work, replace them with output files captured from the real legacy system.\n",
        encoding="utf-8")
