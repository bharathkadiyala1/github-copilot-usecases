"""Modern job (a typical Copilot first draft): exact Decimal maths, round half up.
Usage: python new_job.py <input_csv> <output_csv>
"""
import csv
import sys
from decimal import Decimal, ROUND_HALF_UP

USD_TO_CAD = Decimal("1.35")
CENT = Decimal("0.01")


def convert(amount_text, currency):
    amount = Decimal(amount_text)
    if currency == "USD":
        amount = (amount * USD_TO_CAD).quantize(CENT, rounding=ROUND_HALF_UP)
    return amount


def main(src, dst):
    totals, counts = {}, {}
    with open(src, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["amount"] == "":
                continue
            acct = row["account_id"]
            totals[acct] = totals.get(acct, Decimal("0")) + convert(row["amount"], row["currency"])
            counts[acct] = counts.get(acct, 0) + 1
    with open(dst, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["account_id", "txn_count", "total_cad"])
        for acct in sorted(totals):
            writer.writerow([acct, counts[acct], "%.2f" % totals[acct]])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
