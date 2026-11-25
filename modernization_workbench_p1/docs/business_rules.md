# Business rules (the contract every legacy system and the modern pipeline must follow)

Input record (one transaction): `txn_id, account_id, txn_date, txn_type, amount, currency, channel, memo`.
All data is mock data.

## Row checks (first failing check wins, in this order)
| # | Reason code | Rule |
|---|-------------|------|
| 1 | MISSING_ID | `txn_id` is blank |
| 2 | BAD_ACCOUNT | `account_id` is blank |
| 3 | BAD_DATE | not a real calendar date in the system's format (`YYYYMMDD` fixed file, `YYYY-MM-DD` CSV) |
| 4 | BAD_TYPE | not one of DEP, INT, XFRIN (credits) or WDR, WIRE, POS, XFROUT (debits) |
| 5 | BAD_AMOUNT | not digits with at most 2 decimals (e.g. `ABC`, `1.2.3`, `12.345`, `-5.00`), or zero |
| 6 | BAD_CURRENCY | not `CAD` or `USD` (case sensitive) |
| 7 | DUPLICATE | `txn_id` already used by an earlier GOOD row (first one wins; rejected rows do not count) |

## Calculations (whole cents, never floating point)
- **Channel:** trimmed and upper-cased; blank becomes `UNKNOWN`.
- **USD to CAD:** `cents x 1.35`, rounded to a whole cent with the legacy system's rounding style.
- **ATM fee:** type `WDR`, channel `ATM`, CAD amount over 500.00 -> fee 2.00.
- **Wire fee:** type `WIRE` -> 0.1% of the CAD amount, rounded, at least 5.00 and at most 25.00.
- **Rounding style differs per legacy system:** python/sql/sas = half up, cobol = truncate (no ROUNDED), ssis = half even.

## Output
- `summary.csv`, one row per account sorted by `account_id`:
  `account_id, txn_count, total_credits_cad, total_debits_cad, total_fees_cad, net_cad, last_txn_date, month_end_txn_count, atm_txn_count`
  (`net_cad = credits - debits - fees`; dates as `YYYY-MM-DD`; money with 2 decimals)
- `rejects.csv`: `line_no, txn_id, reason` in input order. `line_no` counts data rows from 1.

## Known quirks worth testing
Leap day 2024-02-29, 2023-02-29 (invalid), rounding ties such as 1.10 USD, wire fee ties, exactly 500.00 ATM,
lowercase `atm`, an id first seen on a bad row then on a good row.
