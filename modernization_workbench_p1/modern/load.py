"""Step 3 - LOAD. Writes the results. Plain CSV files, always.

Delta Lake / Iceberg output is produced by generated PySpark code (see spark_codegen.py).
"""
SUMMARY_COLUMNS = [
    "account_id", "txn_count", "total_credits_cad", "total_debits_cad", "total_fees_cad",
    "net_cad", "last_txn_date", "month_end_txn_count", "atm_txn_count",
]
REJECT_COLUMNS = ["line_no", "txn_id", "reason"]


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(",".join(columns) + "\n")
        for row in rows:
            handle.write(",".join(str(row[c]) for c in columns) + "\n")


def write_outputs(cfg, summary, rejects):
    out_dir = cfg.output_dir / "modern" / cfg.system
    write_csv(out_dir / "summary.csv", SUMMARY_COLUMNS, summary)
    write_csv(out_dir / "rejects.csv", REJECT_COLUMNS, rejects)
    return out_dir
