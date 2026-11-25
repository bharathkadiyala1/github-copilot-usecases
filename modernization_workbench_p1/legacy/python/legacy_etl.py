# legacy_etl.py
# Daily account summary. Written in 2009. Runs from cron. Nobody dares touch it.
# (Intentionally messy: this is the "before" picture of the demo. Do not copy this style.)
import sys, os, calendar

HERE = os.path.dirname(os.path.abspath(__file__))
IN_FILE = sys.argv[1] if len(sys.argv) > 1 else HERE + "/../../data/input/transactions.dat"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else HERE + "/../../output/legacy/python"

f = open(IN_FILE)
lines = f.readlines()
f.close()
ids = {}
acc = {}
rej = []
n = 0
for line in lines:
    n = n + 1
    line = line.rstrip("\r\n")
    while len(line) < 72:
        line = line + " "
    tid = line[0:8].strip()
    a = line[8:18].strip()
    d = line[18:26].strip()
    t = line[26:32].strip()
    amt = line[32:44].strip()
    cur = line[44:47].strip()
    ch = line[47:53].strip().upper()
    if ch == "":
        ch = "UNKNOWN"
    r = ""
    y = m = dd = 0
    if tid == "":
        r = "MISSING_ID"
    elif a == "":
        r = "BAD_ACCOUNT"
    else:
        ok = len(d) == 8 and d.isdigit()
        if ok:
            y = int(d[0:4]); m = int(d[4:6]); dd = int(d[6:8])
            if y < 1 or m < 1 or m > 12:
                ok = False
            elif dd < 1 or dd > calendar.monthrange(y, m)[1]:
                ok = False
        cents = -1
        parts = amt.split(".")
        if amt != "" and len(parts) <= 2 and parts[0].isdigit() and (len(parts) == 1 or (parts[1].isdigit() and 1 <= len(parts[1]) <= 2)):
            cents = int(parts[0]) * 100
            if len(parts) == 2:
                cents = cents + int((parts[1] + "0")[0:2])
        if not ok:
            r = "BAD_DATE"
        elif t not in ("DEP", "INT", "XFRIN", "WDR", "WIRE", "POS", "XFROUT"):
            r = "BAD_TYPE"
        elif cents <= 0:
            r = "BAD_AMOUNT"
        elif cur != "CAD" and cur != "USD":
            r = "BAD_CURRENCY"
        elif tid in ids:
            r = "DUPLICATE"
        else:
            ids[tid] = 1
    if r != "":
        rej.append((n, tid, r))
        continue
    if cur == "USD":
        c = (cents * 135 + 50) // 100
    else:
        c = cents
    fee = 0
    if t == "WDR" and ch == "ATM" and c > 50000:
        fee = 200
    if t == "WIRE":
        fee = (c + 500) // 1000
        if fee < 500:
            fee = 500
        if fee > 2500:
            fee = 2500
    if a not in acc:
        acc[a] = [0, 0, 0, 0, "", 0, 0]
    x = acc[a]
    x[0] = x[0] + 1
    if t in ("DEP", "INT", "XFRIN"):
        x[1] = x[1] + c
    else:
        x[2] = x[2] + c
    x[3] = x[3] + fee
    iso = "%04d-%02d-%02d" % (y, m, dd)
    if iso > x[4]:
        x[4] = iso
    if dd == calendar.monthrange(y, m)[1]:
        x[5] = x[5] + 1
    if ch == "ATM":
        x[6] = x[6] + 1


def fm(c):
    s = ""
    if c < 0:
        s = "-"
        c = -c
    return s + str(c // 100) + "." + ("%02d" % (c % 100))


if not os.path.exists(OUT_DIR):
    os.makedirs(OUT_DIR)
o = open(OUT_DIR + "/summary.csv", "w")
o.write("account_id,txn_count,total_credits_cad,total_debits_cad,total_fees_cad,net_cad,last_txn_date,month_end_txn_count,atm_txn_count\n")
for k in sorted(acc.keys()):
    x = acc[k]
    o.write(k + "," + str(x[0]) + "," + fm(x[1]) + "," + fm(x[2]) + "," + fm(x[3]) + "," + fm(x[1] - x[2] - x[3]) + "," + x[4] + "," + str(x[5]) + "," + str(x[6]) + "\n")
o.close()
o = open(OUT_DIR + "/rejects.csv", "w")
o.write("line_no,txn_id,reason\n")
for q in rej:
    o.write(str(q[0]) + "," + q[1] + "," + q[2] + "\n")
o.close()
print("legacy python done: %d accounts, %d rejects" % (len(acc), len(rej)))
