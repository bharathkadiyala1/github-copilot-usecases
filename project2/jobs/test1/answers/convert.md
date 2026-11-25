"""new_job.py - replacement for test_script.sql (order pricing).
Usage: python new_job.py <input_csv_path> <output_csv_path>
Input is one flat CSV that carries customer, product and order-line columns; it is split into the
three legacy tables first, then the legacy query is applied. All money uses Decimal.
"""
import csv
import re
import sys
from decimal import Decimal, ROUND_HALF_UP, getcontext

getcontext().prec = 28
CENT, TENTH = Decimal("0.01"), Decimal("0.1")
INT_RE = re.compile(r"^[+-]?\d+$")
TIERS = ("BASIC", "SILVER", "GOLD")      # Rule 2 / customer CHECK: allowed tier values (NULL also allowed)

# Rule 16: seeded tax_rate table. QC 0.14975 does not fit numeric(5,4), so it is stored rounded as 0.1498.
TAX_RATE = {"ON": Decimal("0.13"), "BC": Decimal("0.12"), "AB": Decimal("0.05"), "QC": Decimal("0.1498")}


def rnd(x, quant):
    """Rule 5/7/8: ROUND(numeric, n) = ties away from zero (half-up on magnitude); no negative zero."""
    r = x.quantize(quant, rounding=ROUND_HALF_UP)
    return abs(r) if r == 0 else r


def null(s):
    """Load rule: an empty field is NULL."""
    return None if s is None or s == "" else s


def to_int(s):
    """Column type int: anything else is a load reject (returns the marker 'bad')."""
    if s is None:
        return None
    return int(s) if INT_RE.match(s) else "bad"


def to_money(s):
    """Column type numeric(10,2): rounded to 2 decimals on load; non-numeric or out of range is a reject."""
    try:
        d = Decimal(s)
    except Exception:
        return "bad"
    if not d.is_finite():
        return "bad"
    d = rnd(d, CENT)
    return "bad" if abs(d) >= Decimal(10) ** 8 else d


def load(path):
    """Split the flat file into customer / product / order_line tables with the legacy constraints."""
    customers, products, lines, seen = {}, {}, [], set()
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            cid, sku = to_int(null(r["customer_id"])), null(r["sku"])
            # customer table: PK customer_id (first row wins), province NOT NULL, tier CHECK (NULL allowed)
            if cid not in (None, "bad") and cid not in customers:
                tier, prov = null(r["tier"]), null(r["province"])
                if prov is not None and (tier is None or tier in TIERS):
                    customers[cid] = (tier, prov)
            # product table: PK sku (first row wins), price and cost NOT NULL numeric(10,2)
            if sku is not None and sku not in products:
                price, cost = to_money(r["price"]), to_money(r["cost"])
                if price != "bad" and cost != "bad":
                    products[sku] = (price, cost)
            # order_line table: int columns, CHECK qty > 0 (NULL passes), PK (order_id, line_no), FKs (NULL passes)
            oid, ln, qty = to_int(null(r["order_id"])), to_int(null(r["line_no"])), to_int(null(r["qty"]))
            if "bad" in (oid, ln, qty, cid) or oid is None or ln is None:
                continue
            if qty is not None and qty <= 0:
                continue
            if (oid, ln) in seen:
                continue
            seen.add((oid, ln))
            lines.append((oid, ln, cid, sku, qty, null(r["promo_code"])))
    # FK check: a non-NULL customer_id / sku must exist in its table, otherwise the row is rejected
    return customers, products, [l for l in lines
                                 if (l[2] is None or l[2] in customers) and (l[3] is None or l[3] in products)]


def main():
    customers, products, lines = load(sys.argv[1])
    groups = {}                                  # Rule 10: (order_id, customer_id, province) -> [subtotal, total_cost]
    for oid, _ln, cid, sku, qty, promo in lines:
        if cid is None or sku is None:           # Rule 2: inner joins drop lines with NULL customer or sku
            continue
        tier, prov = customers[cid]
        price, cost = products[sku]
        net = cost_line = None
        if qty is not None:                      # Rule 15 / NULL qty: net and cost stay NULL
            # Rule 4: volume discount per line
            vol = Decimal("0.10") if qty >= 50 else Decimal("0.05") if qty >= 20 else Decimal("0.02") if qty >= 10 else Decimal(0)
            # Rule 5: tier discount (BASIC / NULL = 0)
            tier_pct = {"GOLD": Decimal("0.05"), "SILVER": Decimal("0.02")}.get(tier, Decimal(0))
            # Rule 6: promo discount, exact case-sensitive match, unknown code = 0
            promo_pct = {"SAVE10": Decimal("0.10"), "SAVE5": Decimal("0.05")}.get(promo, Decimal(0))
            # Rule 7: discounts stack, capped at 15%
            disc = min(vol + tier_pct + promo_pct, Decimal("0.15"))
            # Rule 8: line net rounded to the cent, ties away from zero; Rule 9: line cost not rounded
            net = rnd(qty * price * (1 - disc), CENT)
            cost_line = qty * cost
        g = groups.setdefault((oid, cid, prov), [None, None])
        if net is not None:                      # Rule 10: SUM ignores NULLs; all-NULL stays NULL
            g[0] = net if g[0] is None else g[0] + net
            g[1] = cost_line if g[1] is None else g[1] + cost_line

    out = []
    # Rule 15: order by order_id (ties are undefined in the legacy; customer_id is used here)
    for (oid, _cid, prov), (sub, total_cost) in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        if prov not in TAX_RATE:                 # Rule 2: inner join to tax_rate, case-sensitive; unknown province drops the order
            continue
        # Rule 11: shipping 0 when subtotal >= 100 else 9.99 (NULL subtotal falls to 9.99)
        shipping = Decimal(0) if (sub is not None and sub >= 100) else Decimal("9.99")
        if sub is None:                          # NULL subtotal: tax and margin NULL, flag OK
            out.append([oid, "", format(shipping, "f"), "", "", "OK"])
            continue
        # Rule 12: tax on goods plus shipping at the province rate, rounded to the cent
        tax = rnd((sub + shipping) * TAX_RATE[prov], CENT)
        # Rule 13: margin % on goods only, 1 decimal; subtotal 0 gives NULL
        # Rule 14: REVIEW when the unrounded margin ratio is strictly below 10%
        if sub == 0:
            margin_pct, flag = "", "OK"
        else:
            ratio = (sub - total_cost) / sub
            margin_pct = format(rnd(ratio * 100, TENTH), "f")
            flag = "REVIEW" if ratio < Decimal("0.10") else "OK"
        out.append([oid, format(sub, "f"), format(shipping, "f"), format(tax, "f"), margin_pct, flag])

    with open(sys.argv[2], "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")   # ASSUMPTION: LF line endings, NULL written as empty
        w.writerow(["order_id", "subtotal", "shipping", "tax", "margin_pct", "margin_flag"])
        w.writerows(out)


if __name__ == "__main__":
    main()