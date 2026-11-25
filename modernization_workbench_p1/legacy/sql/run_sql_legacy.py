"""Runs legacy_etl.sql with the built-in sqlite3 module (no database server needed).

Usage: python run_sql_legacy.py [input.csv] [output_dir]
"""
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_FILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "data", "input", "transactions.csv")
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "..", "output", "legacy", "sql")

db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE raw (line_no INTEGER, txn_id TEXT, account_id TEXT, txn_date TEXT, "
           "txn_type TEXT, amount TEXT, currency TEXT, channel TEXT, memo TEXT)")
with open(IN_FILE, newline="", encoding="utf-8") as handle:
    reader = csv.reader(handle)
    next(reader)                                   # skip header
    for line_no, r in enumerate(reader, start=1):
        db.execute("INSERT INTO raw VALUES (?,?,?,?,?,?,?,?,?)", [line_no] + r)

with open(os.path.join(HERE, "legacy_etl.sql"), encoding="utf-8") as handle:
    db.executescript(handle.read())

os.makedirs(OUT_DIR, exist_ok=True)
for table, name in (("summary_out", "summary.csv"), ("rejects_out", "rejects.csv")):
    cursor = db.execute("SELECT * FROM %s ORDER BY %s" % (table, "account_id" if table == "summary_out" else "line_no"))
    headers = [d[0] for d in cursor.description]
    with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8", newline="") as out:
        out.write(",".join(headers) + "\n")
        for r in cursor:
            out.write(",".join(str(v) for v in r) + "\n")
print("legacy sql done")
