"""New job: replacement for txn_summary.sas. Usage: python new_job.py <input_csv_path> <output_csv_path>
Reproduces the SAS behaviour, quirks included, using Decimal for all money.
"""
import csv
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

RATE = Decimal("1.35")      # Rule: fixed USD->CAD rate, no date logic
CENT = Decimal("0.01")      # Rule: SAS round(x, 0.01) = nearest cent, ties away from zero (half-up on magnitude)
SAS_CHAR_LEN = 8            # Rule: SAS list input without informat gives $8 char vars -> values truncated to 8 chars (ASSUMPTION: default length)


def parse_amount(text):
    """Rule: numeric input; blank or unparseable values become SAS missing (note only, no abort) and the row is dropped."""
    s = text.strip()
    if s in ("", ".") or "_" in s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None     # nan/inf are not valid SAS numeric input -> missing


def main():
    totals, counts = {}, {}

    # Rule: infile with dsd and firstobs=2 -> comma delimited (quotes honoured), first line skipped regardless of its content;
    # fields are read BY POSITION: txn_id, account_id, amount, currency (header names are ignored).
    with open(sys.argv[1], newline="") as f:
        reader = csv.reader(f)
        next(reader, None)                                   # firstobs=2
        for row in reader:
            row = (row + [""] * 4)[:4]                       # ASSUMPTION: short rows are padded with missing values
            account_id = row[1].strip()[:SAS_CHAR_LEN]
            currency = row[3].strip()[:SAS_CHAR_LEN]

            # Rule: missing amount -> row deleted (not counted, not totalled, no reject output).
            amount = parse_amount(row[2])
            if amount is None:
                continue

            # Rule: only currency = "USD" (case-sensitive; trailing blanks ignored as in SAS) is converted,
            # rounded per row to the cent, ties away from zero. Any other currency is added as-is, unrounded.
            if currency == "USD":
                amount_cad = (amount * RATE).quantize(CENT, rounding=ROUND_HALF_UP)
            else:
                amount_cad = amount

            # Rule: group by account_id (blank id is its own group); count(*) counts every kept row.
            totals[account_id] = totals.get(account_id, Decimal(0)) + amount_cad
            counts[account_id] = counts.get(account_id, 0) + 1

    # Rule: output ordered by account_id in ASCII/code-point order; accounts with only dropped rows are absent.
    # Rule: total_cad uses format 12.2 -> exactly 2 decimals, '.' separator, no thousands separator, rounded half away from zero.
    with open(sys.argv[2], "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")               # ASSUMPTION: LF line endings
        w.writerow(["account_id", "txn_count", "total_cad"])
        for acct in sorted(totals):
            total = totals[acct].quantize(CENT, rounding=ROUND_HALF_UP)
            text = format(total, "f")
            if total == 0:
                text = "0.00"                                # ASSUMPTION: a zero total prints without a minus sign
            w.writerow([acct, counts[acct], text])


if __name__ == "__main__":
    main()