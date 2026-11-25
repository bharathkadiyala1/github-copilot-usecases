"""Creates MOCK bank transactions in two formats: fixed-width (.dat) and CSV.

Everything is random and fake. A few hand-made edge cases are added at the end so the
parity check has something interesting to find (rounding ties, bad dates, duplicates...).
"""
import random
from datetime import date, timedelta

LAYOUT = [("txn_id", 8), ("account_id", 10), ("txn_date", 8), ("txn_type", 6),
          ("amount", 12), ("currency", 3), ("channel", 6), ("memo", 19)]
TYPES = ["DEP", "INT", "XFRIN", "WDR", "WIRE", "POS", "XFROUT"]
CHANNELS = ["ATM", "ONLINE", "BRANCH", "POS", ""]
MEMOS = ["", "payroll", "rent", "groceries", "refund", "gift", "utilities", "transfer"]

COLUMNS = ["txn_id", "account_id", "txn_date", "txn_type", "amount", "currency", "channel", "memo"]


def row(txn_id, account, txn_date, txn_type, amount, currency, channel="ONLINE", memo=""):
    return dict(zip(COLUMNS, [txn_id, account, txn_date, txn_type, amount, currency, channel, memo]))


def edge_cases():
    a1, a2 = "EDG0000001", "EDG0000002"
    return [
        # rounding ties: 1.10 USD * 1.35 = 1.485 and 2.30 * 1.35 = 3.105
        row("E0000001", a1, "20240229", "DEP", "1.10", "USD"),
        row("E0000002", a1, "20240229", "DEP", "2.30", "USD"),
        # wire fees: tie inside the limits, minimum, maximum, normal
        row("E0000003", a1, "20240315", "WIRE", "12005.00", "CAD"),
        row("E0000004", a1, "20240315", "WIRE", "1234.56", "CAD"),
        row("E0000005", a1, "20240315", "WIRE", "50000.50", "CAD"),
        row("E0000006", a1, "20240315", "WIRE", "20000.00", "CAD", "BRANCH"),
        # ATM fee threshold: exactly 500 (no fee), 500.01 (fee), USD converted, lowercase channel
        row("E0000007", a2, "20240430", "WDR", "500.00", "CAD", "ATM"),
        row("E0000008", a2, "20240430", "WDR", "500.01", "CAD", "ATM"),
        row("E0000009", a2, "20240630", "WDR", "400.00", "USD", "ATM"),
        row("E0000010", a2, "20240630", "WDR", "600.00", "CAD", "atm"),
        row("E0000011", a2, "20241231", "POS", "19.99", "CAD", ""),
        # rejects
        row("E0000012", a2, "20230229", "DEP", "10.00", "CAD"),     # not a leap year
        row("E0000013", a2, "20240431", "DEP", "10.00", "CAD"),     # April has 30 days
        row("E0000014", a2, "2024105", "DEP", "10.00", "CAD"),      # wrong date shape
        row("E0000015", a2, "20240510", "DEP", "ABC", "CAD"),
        row("E0000016", a2, "20240510", "DEP", "0.00", "CAD"),
        row("E0000017", a2, "20240510", "DEP", "12.345", "CAD"),
        row("E0000018", a2, "20240510", "DEP", "1.2.3", "CAD"),
        row("E0000019", a2, "20240510", "DEP", "-5.00", "CAD"),
        row("E0000020", a2, "20240510", "XXX", "5.00", "CAD"),
        row("E0000021", a2, "20240510", "DEP", "5.00", "EUR"),
        row("E0000022", a2, "20240510", "DEP", "5.00", "usd"),
        row("", a2, "20240510", "DEP", "5.00", "CAD"),               # missing id
        row("E0000024", "", "20240510", "DEP", "5.00", "CAD"),       # missing account
        row("T0000001", a2, "20240510", "DEP", "5.00", "CAD"),       # duplicate of a good row
        # an id that first appears on a BAD row and then on a good row: the good row is kept
        row("E0000050", a2, "20240510", "DEP", "ABC", "CAD"),
        row("E0000050", a2, "20240510", "DEP", "7.00", "CAD"),
    ]


def make_rows(cfg):
    rng = random.Random(cfg.mock_seed)
    accounts = max(5, cfg.mock_rows // 20)
    rows = []
    for i in range(1, cfg.mock_rows + 1):
        day = date(2024, 1, 1) + timedelta(days=rng.randint(0, 365))
        rows.append(row("T%07d" % i, "ACC%07d" % rng.randint(1, accounts), day.strftime("%Y%m%d"),
                        rng.choice(TYPES), "%d.%02d" % (rng.randint(1, 3000), rng.randint(0, 99)),
                        "USD" if rng.random() < 0.15 else "CAD", rng.choice(CHANNELS), rng.choice(MEMOS)))
    return rows + edge_cases()


def iso(text):
    """20240229 -> 2024-02-29. Anything that is not 8 digits is left alone."""
    if len(text) == 8 and text.isdigit():
        return "%s-%s-%s" % (text[:4], text[4:6], text[6:])
    return text


def generate(cfg):
    rows = make_rows(cfg)
    cfg.input_fixed.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.input_fixed, "w", encoding="utf-8", newline="") as handle:
        for r in rows:
            handle.write("".join(r[name].ljust(width) for name, width in LAYOUT) + "\n")
    with open(cfg.input_csv, "w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(COLUMNS) + "\n")
        for r in rows:
            r = dict(r, txn_date=iso(r["txn_date"]))
            handle.write(",".join(r[c] for c in COLUMNS) + "\n")
    return len(rows)


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from modern.config import Config
    print("rows written:", generate(Config()))
