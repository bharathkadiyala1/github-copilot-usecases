"""New job (modern replacement for legacy_etl.py).
Usage: python new_job.py <input_csv_path> <output_csv_path>
Reproduces legacy behaviour exactly, quirks included.
"""
import csv
import sys
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

RATE = 1.35                    # Rule 6: fixed USD->CAD rate, no date logic (kept as the same binary double)
CENT = Decimal("0.01")


def usd_to_cad(amt_f):
    """Rules 6/7: per-row conversion, rounded to 2 decimals, half-even on the binary value.
    Decimal(float) is exact, so the product of the two doubles is exact; float() then rounds it
    to the nearest double (as the legacy float multiply did), and the quantize mirrors round(x, 2)."""
    product = float(Decimal(amt_f) * Decimal(RATE))
    if product != product or product in (float("inf"), float("-inf")):
        return Decimal(product)                     # Rule 5: nan/inf pass through untouched
    return Decimal(product).quantize(CENT, rounding=ROUND_HALF_EVEN)


def format_total(total):
    """Rule 12: two decimals, '.' separator, no thousands separator, half-even,
    '-0.00' possible, nan/inf written as literal text."""
    if not total.is_finite():
        return "%.2f" % float(total)
    return format(total.quantize(CENT, rounding=ROUND_HALF_EVEN), "f")


def main():
    totals, counts = {}, {}

    # Decimal context: high precision so sums of exact double values stay exact; traps off so
    # inf + -inf gives NaN like float arithmetic (Rule 5).
    with localcontext() as ctx:
        ctx.prec = 2000
        ctx.traps[__import__("decimal").InvalidOperation] = False

        # Rule 1: read the whole input before writing anything (Rule 15).
        # Rule 2: columns accessed by exact name; missing header -> KeyError.
        with open(sys.argv[1], newline="") as f:
            for row in csv.DictReader(f):
                # Rule 3: exactly-empty amount is silently skipped (not counted, no reject output).
                if row["amount"] == "":
                    continue
                # Rules 4/5: parsed with float() (accepts " 5 ", 1e2, 1_000, nan, inf; "1,000" or " " crash).
                amt_f = float(row["amount"])
                # Rule 6: only exact, case-sensitive "USD" is converted.
                # Rule 8: every other currency value is added as-is, unrounded, as if CAD.
                if row["currency"] == "USD":
                    amt = usd_to_cad(amt_f)
                else:
                    amt = Decimal(amt_f)
                # Rule 9: raw account_id string is the key (no trim/case fold; "" is a valid account).
                acct = row["account_id"]
                totals[acct] = totals.get(acct, Decimal(0)) + amt
                # Rule 10: count every non-skipped row (zero, negative, non-USD included).
                counts[acct] = counts.get(acct, 0) + 1

        # Rule 15: output opened only after input is fully processed.
        with open(sys.argv[2], "w", newline="") as f:
            w = csv.writer(f)                        # Rule 14/Outputs: default dialect (\r\n, minimal quoting)
            w.writerow(["account_id", "txn_count", "total_cad"])
            # Rule 13: plain string (code-point) ordering of account_id.
            # Rule 11: accounts with only blank-amount rows never entered the dicts, so they are absent.
            for acct in sorted(totals):
                w.writerow([acct, counts[acct], format_total(totals[acct])])


if __name__ == "__main__":
    main()