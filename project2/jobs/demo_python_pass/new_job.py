"""Modern job: same results as the legacy job, including its float rounding quirk.
Usage: python new_job.py <input_csv> <output_csv>
"""
import csv
import sys

USD_TO_CAD = 1.35   # rule: USD amounts convert at 1.35


def convert(amount_text, currency):
    amount = float(amount_text)
    if currency == "USD":
        amount = round(amount * USD_TO_CAD, 2)    # kept as float on purpose: legacy parity
    return amount


def main(src, dst):
    totals, counts = {}, {}
    with open(src, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["amount"] == "":               # rule: blank amounts are skipped
                continue
            acct = row["account_id"]
            totals[acct] = totals.get(acct, 0.0) + convert(row["amount"], row["currency"])
            counts[acct] = counts.get(acct, 0) + 1
    with open(dst, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["account_id", "txn_count", "total_cad"])
        for acct in sorted(totals):               # rule: sorted by account
            writer.writerow([acct, counts[acct], "%.2f" % totals[acct]])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
