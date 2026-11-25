"""Step 2 - TRANSFORM. All business rules live here. See docs/business_rules.md.

Money is handled as whole cents (integers) so there are no floating point surprises.
"""
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal

log = logging.getLogger("workbench")

AMOUNT_PATTERN = re.compile(r"^[0-9]+(\.[0-9]{1,2})?$")


def format_cents(cents):
    """12345 -> '123.45', -5 -> '-0.05'"""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "%s%d.%02d" % (sign, cents // 100, cents % 100)


def parse_amount_cents(text):
    """'12.5' -> 1250. Returns None when the text is not a valid amount."""
    if not AMOUNT_PATTERN.match(text):
        return None
    whole, _, fraction = text.partition(".")
    return int(whole) * 100 + int((fraction + "00")[:2])


def parse_date(text, date_format):
    """Strict date parse. Returns a date, or None if invalid (e.g. 2023-02-29)."""
    try:
        parsed = datetime.strptime(text, date_format)
    except ValueError:
        return None
    if parsed.strftime(date_format) != text:   # rejects things like 2024-1-5
        return None
    return parsed.date()


def round_to_int(value, rounding_mode):
    return int(value.quantize(Decimal(1), rounding=rounding_mode))


def to_cad_cents(cents, currency, cfg):
    if currency == "CAD":
        return cents
    return round_to_int(Decimal(cents) * cfg.fx, cfg.rounding_mode)


def fee_cents(txn_type, channel, cad_cents, cfg):
    if txn_type == "WDR" and channel == "ATM" and cad_cents > cfg.atm_threshold_cents:
        return cfg.atm_fee_cents
    if txn_type == "WIRE":
        fee = round_to_int(Decimal(cad_cents) * cfg.wire_rate, cfg.rounding_mode)
        return min(max(fee, cfg.wire_min_cents), cfg.wire_max_cents)
    return 0


def validate(row, cfg, seen_ids):
    """Return (reason, parsed). reason is None when the row is good."""
    if row["txn_id"] == "":
        return "MISSING_ID", None
    if row["account_id"] == "":
        return "BAD_ACCOUNT", None
    txn_date = parse_date(row["txn_date"], cfg.date_format)
    if txn_date is None:
        return "BAD_DATE", None
    if row["txn_type"] not in cfg.credit_types | cfg.debit_types:
        return "BAD_TYPE", None
    cents = parse_amount_cents(row["amount"])
    if cents is None or cents <= 0:
        return "BAD_AMOUNT", None
    if row["currency"] not in cfg.currencies:
        return "BAD_CURRENCY", None
    if row["txn_id"] in seen_ids:
        return "DUPLICATE", None
    seen_ids.add(row["txn_id"])    # only good rows register their id

    cad = to_cad_cents(cents, row["currency"], cfg)
    channel = row["channel"].upper() or "UNKNOWN"
    return None, {
        "account_id": row["account_id"],
        "date": txn_date,
        "is_credit": row["txn_type"] in cfg.credit_types,
        "cad": cad,
        "fee": fee_cents(row["txn_type"], channel, cad, cfg),
        "is_atm": channel == "ATM",
    }


def process(rows, cfg):
    """Run every row through the rules. Returns (summary_rows, reject_rows)."""
    accounts = {}
    rejects = []
    seen_ids = set()
    count = 0
    for row in rows:
        count += 1
        if cfg.log_every and count % cfg.log_every == 0:
            log.info("processed %d rows", count)
        reason, good = validate(row, cfg, seen_ids)
        if reason:
            rejects.append({"line_no": row["line_no"], "txn_id": row["txn_id"], "reason": reason})
            continue
        acc = accounts.setdefault(good["account_id"], {
            "count": 0, "credits": 0, "debits": 0, "fees": 0,
            "last": good["date"], "month_end": 0, "atm": 0})
        acc["count"] += 1
        acc["credits" if good["is_credit"] else "debits"] += good["cad"]
        acc["fees"] += good["fee"]
        acc["last"] = max(acc["last"], good["date"])
        acc["month_end"] += 1 if (good["date"] + timedelta(days=1)).day == 1 else 0
        acc["atm"] += 1 if good["is_atm"] else 0

    summary = []
    for account_id in sorted(accounts):
        a = accounts[account_id]
        summary.append({
            "account_id": account_id,
            "txn_count": a["count"],
            "total_credits_cad": format_cents(a["credits"]),
            "total_debits_cad": format_cents(a["debits"]),
            "total_fees_cad": format_cents(a["fees"]),
            "net_cad": format_cents(a["credits"] - a["debits"] - a["fees"]),
            "last_txn_date": a["last"].isoformat(),
            "month_end_txn_count": a["month_end"],
            "atm_txn_count": a["atm"],
        })
    log.info("rows read=%d accounts=%d rejects=%d", count, len(summary), len(rejects))
    return summary, rejects
