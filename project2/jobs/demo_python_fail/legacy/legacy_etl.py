"""Legacy ETL (mock). Sums transactions per account in CAD.
Hardcoded style, float maths, round() on a float: typical legacy debt.
Usage: python legacy_etl.py <input_csv> <output_csv>
"""
import csv, sys

RATE = 1.35                       # USD -> CAD, hardcoded
totals, counts = {}, {}
with open(sys.argv[1], newline="") as f:
    for row in csv.DictReader(f):
        if row["amount"] == "":                       # silently skips blank amounts
            continue
        amt = float(row["amount"])
        if row["currency"] == "USD":
            amt = round(amt * RATE, 2)                # float rounding quirk
        totals[row["account_id"]] = totals.get(row["account_id"], 0.0) + amt
        counts[row["account_id"]] = counts.get(row["account_id"], 0) + 1

with open(sys.argv[2], "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["account_id", "txn_count", "total_cad"])
    for acct in sorted(totals):
        w.writerow([acct, counts[acct], "%.2f" % totals[acct]])
