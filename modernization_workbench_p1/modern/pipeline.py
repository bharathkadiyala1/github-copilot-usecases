"""Runs extract -> transform -> load, logs progress and writes a small health file."""
import json
import logging
import time
from datetime import datetime

from modern import extract, load, spark_codegen, transform

log = logging.getLogger("workbench")


def setup_logging(cfg):
    if log.handlers:
        return
    cfg.audit_dir.mkdir(parents=True, exist_ok=True)
    log.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(cfg.audit_dir / "pipeline.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def run(cfg):
    setup_logging(cfg)
    started = time.time()
    log.info("modern pipeline start system=%s format=%s rounding=%s", cfg.system, cfg.input_format, cfg.rounding)
    status = {"system": cfg.system, "status": "ok", "started": datetime.now().isoformat(timespec="seconds")}
    try:
        rows = extract.read_rows(cfg)
        summary, rejects = transform.process(rows, cfg)
        out_dir = load.write_outputs(cfg, summary, rejects)
        status.update(accounts=len(summary), rejects=len(rejects), output=str(out_dir))
        if cfg.target in ("delta", "iceberg"):
            status["spark"] = spark_codegen.generate_and_maybe_run(cfg)
    except Exception as error:               # health file must say what failed
        status.update(status="failed", error=repr(error))
        log.exception("pipeline failed")
        raise
    finally:
        status["seconds"] = round(time.time() - started, 2)
        health_dir = cfg.output_dir / "modern" / cfg.system
        health_dir.mkdir(parents=True, exist_ok=True)
        (health_dir / "run_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    log.info("modern pipeline done in %ss", status["seconds"])
    return status


def health(cfg):
    """Return the last run status for this system, or a 'never run' message."""
    path = cfg.output_dir / "modern" / cfg.system / "run_status.json"
    if not path.exists():
        return {"system": cfg.system, "status": "never run"}
    return json.loads(path.read_text(encoding="utf-8"))
