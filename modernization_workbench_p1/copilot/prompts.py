"""Builds the prompts we send to Copilot. The wording lives in copilot/prompts/*.txt - edit them freely."""
from modern.config import ROOT

# Which legacy files belong to each system (shown to Copilot as the source to analyse).
SOURCES = {
    "python": ["legacy/python/legacy_etl.py"],
    "sql": ["legacy/sql/legacy_etl.sql", "legacy/sql/run_sql_legacy.py"],
    "cobol": ["legacy/cobol/TXNREC.cpy", "legacy/cobol/TXNSUM.cbl"],
    "sas": ["legacy/sas/txn_summary.sas"],
    "ssis": ["legacy/ssis/txn_summary_package.xml"],
}


def read_source(system):
    parts = []
    for relative in SOURCES[system]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        parts.append("### FILE: %s\n%s" % (relative, text))
    return "\n\n".join(parts)


def build_prompt(step, cfg):
    template = (ROOT / "copilot" / "prompts" / (step + ".txt")).read_text(encoding="utf-8")
    target = cfg.target if cfg.target in ("delta", "iceberg") else "delta"
    rules = (ROOT / "docs" / "business_rules.md").read_text(encoding="utf-8")
    values = {"{system}": cfg.system, "{source}": read_source(cfg.system),
              "{rules}": rules, "{target}": "Delta Lake" if target == "delta" else "Iceberg"}
    for key, value in values.items():
        template = template.replace(key, value)
    return template
