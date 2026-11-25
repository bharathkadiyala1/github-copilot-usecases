"""A small stand-in for legacy systems we cannot run here (COBOL, SAS, SSIS).

It is written separately from modern/ (integer math only, no Decimal) so it can act as an
independent cross-check. The output is SIMULATED: in real life you would capture the real
output files from the legacy system and drop them into golden/<system>/.
"""
import calendar
from datetime import date

TYPES_CREDIT = ("DEP", "INT", "XFRIN")
TYPES_ALL = TYPES_CREDIT + ("WDR", "WIRE", "POS", "XFROUT")


def divide(top, bottom, mode):
    """Integer divide with the rounding style of the legacy system."""
    q, r = divmod(top, bottom)
    if mode == "ROUND_DOWN":
        return q
    if mode == "ROUND_HALF_UP":
        return q + (1 if 2 * r >= bottom else 0)
    return q + (1 if (2 * r > bottom or (2 * r == bottom and q % 2 == 1)) else 0)   # half even


def money(c):
    return ("-" if c < 0 else "") + "%d.%02d" % (abs(c) // 100, abs(c) % 100)


def parse_cents(text):
    digits = text.replace(".", "", 1)
    if not digits.isdigit() or text.startswith(".") or text.endswith("."):
        return None
    whole, _, frac = text.partition(".")
    if len(frac) > 2:
        return None
    return int(whole) * 100 + int((frac + "00")[:2])


def simulate(fixed_path, mode):
    """Returns (summary_text, rejects_text) in the standard output format."""
    seen, accounts, rejects = set(), {}, []
    with open(fixed_path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.rstrip("\r\n").ljust(72)
            tid, acct, dtxt, typ = line[0:8].strip(), line[8:18].strip(), line[18:26].strip(), line[26:32].strip()
            amt, cur, chan = line[32:44].strip(), line[44:47].strip(), line[47:53].strip().upper() or "UNKNOWN"
            reason, day = None, None
            if not tid:
                reason = "MISSING_ID"
            elif not acct:
                reason = "BAD_ACCOUNT"
            else:
                try:
                    day = date(int(dtxt[:4]), int(dtxt[4:6]), int(dtxt[6:8])) if len(dtxt) == 8 and dtxt.isdigit() else None
                except ValueError:
                    day = None
                cents = parse_cents(amt)
                if day is None:
                    reason = "BAD_DATE"
                elif typ not in TYPES_ALL:
                    reason = "BAD_TYPE"
                elif not cents:
                    reason = "BAD_AMOUNT"
                elif cur not in ("CAD", "USD"):
                    reason = "BAD_CURRENCY"
                elif tid in seen:
                    reason = "DUPLICATE"
            if reason:
                rejects.append("%d,%s,%s" % (line_no, tid, reason))
                continue
            seen.add(tid)
            cad = divide(cents * 135, 100, mode) if cur == "USD" else cents
            fee = 0
            if typ == "WDR" and chan == "ATM" and cad > 50000:
                fee = 200
            elif typ == "WIRE":
                fee = min(max(divide(cad, 1000, mode), 500), 2500)
            a = accounts.setdefault(acct, [0, 0, 0, 0, day, 0, 0])
            a[0] += 1
            a[1 if typ in TYPES_CREDIT else 2] += cad
            a[3] += fee
            a[4] = max(a[4], day)
            a[5] += 1 if day.day == calendar.monthrange(day.year, day.month)[1] else 0
            a[6] += 1 if chan == "ATM" else 0
    lines = ["account_id,txn_count,total_credits_cad,total_debits_cad,total_fees_cad,net_cad,last_txn_date,month_end_txn_count,atm_txn_count"]
    for k in sorted(accounts):
        a = accounts[k]
        lines.append(",".join([k, str(a[0]), money(a[1]), money(a[2]), money(a[3]), money(a[1] - a[2] - a[3]),
                               a[4].isoformat(), str(a[5]), str(a[6])]))
    return "\n".join(lines) + "\n", "line_no,txn_id,reason\n" + "".join(r + "\n" for r in rejects)
