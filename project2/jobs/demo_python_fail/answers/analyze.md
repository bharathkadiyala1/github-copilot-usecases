I'll run the legacy logic on a few edge inputs first, so the rounding examples are verified.

Line numbers below are for `legacy_etl.py` as pasted (23 lines, blank lines included). I ran the float, rounding and parsing behaviours on sample values before writing this, and I mark anything the source doesn't settle as UNKNOWN.

## 1. Business rules

1. **Scope.** The job reads one CSV of transactions and writes one row per account. It reads the whole file before it writes anything.
2. **Required columns.** The input needs the headers `account_id`, `amount` and `currency`, with exact spelling and case. A missing header stops the run with a `KeyError`. Any other columns are ignored (UNKNOWN whether the real file has any).
3. **Blank amount.** A row whose `amount` is exactly `""` is skipped. It is not counted, not totalled and not reported.
4. **Whitespace-only amount.** An amount like `" "` is not treated as blank. It crashes the run with a `ValueError`.
5. **Amount parsing.** The amount is read with Python `float()`. These forms are accepted:
   - Padded values such as `" 5 "`.
   - Exponent notation such as `1e2`.
   - Underscored values such as `1_000`.
   - `nan` and `inf`.
   - Negative and zero values.

   A value with a comma separator such as `1,000` crashes the run.
6. **USD conversion.** A row with `currency == "USD"` (exact, case-sensitive) is converted to CAD as `round(amount * 1.35, 2)`. The rate is fixed at 1.35 and does not depend on date.
7. **Rounding happens per row, on a binary float.**
   - Each USD row is rounded to 2 decimals before it is added to the total.
   - The result is not half-up. For example, 0.70 USD × 1.35 is 0.945 as a float and rounds to **0.94**, where half-up gives 0.95.
   - Among 1,999 USD amounts from 0.01 to 19.99, 17 differ from half-up.
8. **Every other currency is treated as CAD.** Any value other than exactly `USD` (`CAD`, `EUR`, `usd`, blank) is added as-is, with no conversion, no rounding and no validation.
9. **Accumulation.** Totals are plain float sums per `account_id`. The account ID is used as the raw string, with no trimming or case folding. A blank account ID `""` is a valid account.
10. **Count.** `txn_count` is the number of non-skipped rows for the account. It includes zero, negative and non-USD rows.
11. **Account selection.** An account whose rows all have blank amounts never appears in the output.
12. **Final formatting.** The total is written with `"%.2f"` on the float sum, which rounds again from the binary value. Examples:
    - `1.005` gives `1.00`.
    - `2.675` gives `2.67`.
    - A tiny negative sum prints `-0.00`.
    - `nan` and `inf` print as the literal text `nan` and `inf`.
13. **Ordering.** Output rows are sorted by `account_id` as a string (code-point order). For example `""`, `10`, `2`, `B1`, `a1`: `10` comes before `2`, and uppercase comes before lowercase.
14. **Rejects.** There is no reject file, log or exit-code signal. Blank amounts vanish silently. Any other bad row aborts the whole run with a traceback.
15. **Failure safety.** The output file is opened only after all input is read, so a parse failure leaves any existing output untouched.

## 2. Inputs and outputs

| Direction | Name | Detail |
|---|---|---|
| Input | CLI arg 1 | Path to input CSV (line 9). Missing arg gives `IndexError`. |
| Input | CLI arg 2 | Path to output CSV (line 19). |
| Input | CSV format | Read with `csv.DictReader` defaults: comma delimiter, double-quote, header row required. Encoding is the platform default (UNKNOWN which one is intended). A UTF-8 BOM would corrupt the first header name and cause a `KeyError`. |
| Input | `account_id` | String, used raw. Allowed values are UNKNOWN. |
| Input | `amount` | String parsed as float. Expected format is UNKNOWN (decimal separator, max decimals, sign convention). |
| Input | `currency` | String. Only `USD` is recognised. The full value set is UNKNOWN. |
| Output | CSV file | `csv.writer` defaults, so `\r\n` line endings and minimal quoting. Platform-default encoding. |
| Output | `account_id` | String, copied from the input. |
| Output | `txn_count` | Integer, no padding. |
| Output | `total_cad` | Text with exactly 2 decimals, `.` as the separator, no thousands separator, and `-` for negatives. |
| Output | Header row | Always written: `account_id,txn_count,total_cad`. A header-only input, or an empty file, gives a header-only output. |
| Output | stdout / stderr / exit code | Nothing is printed. Exit code is 0, except on a crash. |

## 3. Technical debt register

| Issue | Where | Risk | Suggested fix |
|---|---|---|---|
| Float arithmetic for money | L13, L16, L23 | Drift and wrong cents. Sums such as 0.1×10 only look right after formatting. | Use `Decimal` or integer cents end to end. |
| `round()` on a float, half-even and binary | L15 | Wrong results versus finance expectation (0.945 becomes 0.94). | `Decimal.quantize` with an explicit, documented rounding mode. Confirm with the business which mode is correct (UNKNOWN). |
| Per-row rounding of USD but not of other rows | L15 | Inconsistent. Totals depend on row granularity. | Decide and document the rule, whether per row or per total. Apply it to all currencies. |
| Hardcoded FX rate, no date or source | L7 | Stale or wrong rate, no audit trail. | Make it config or reference data, with an effective date. |
| Only `USD` recognised, everything else treated as CAD | L14 | An EUR or GBP amount is silently added as CAD. This is a wrong-money risk. | Whitelist currencies. Reject or quarantine unknown codes. |
| Exact, case-sensitive currency match | L14 | `usd` or `USD ` is not converted. | Normalise (trim, upper-case) and then validate. |
| Silent skip of blank amounts | L11–12 | Lost transactions with no trace, and accounts disappear. | Write them to a reject file and a log, with counts. |
| No error handling or reject path | L13 | One bad row (`1,000`, `" "`, missing field) kills the run. | Per-row validation with a reject file and a threshold. |
| `nan` / `inf` accepted | L13 | Poisoned totals, written as text. | Reject non-finite values. |
| No account_id validation or normalisation | L16–17 | Whitespace or case variants split an account. A blank ID becomes a real account. | Trim, validate, and reject blank IDs (rule UNKNOWN). |
| Missing columns or short rows | L11, L14 | `KeyError`, or `TypeError` when a short row gives `None`. | Check the header up front. Handle short rows explicitly. |
| String sort of account IDs | L22 | `10` before `2`. Order may not match what downstream expects (UNKNOWN). | Confirm the intended order. Make it explicit. |
| `-0.00` and the `nan` / `inf` text can be written | L23 | Downstream parsers may break. | Normalise zero. Validate before writing. |
| Implicit encoding and line endings | L9, L19 | Different results across OS or locale. BOM breaks the header. | Set `encoding="utf-8-sig"` on read and an explicit encoding and `lineterminator` on write. |
| Module-level script, no functions, no `__main__` guard | whole file | Untestable. Importing it runs the job. | Split into parse, convert, aggregate and write functions. Add a CLI entry point. |
| No argument validation, no usage message | L9, L19 | Bare `IndexError`. | Use `argparse`. |
| Output not written atomically | L19–23 | A mid-write failure leaves a partial file. | Write to a temp file, then rename. |
| No logging, row counts or reconciliation | whole file | Cannot prove input equals output. | Log rows read, skipped, rejected and written. |
| No tests | n/a | Rewrite parity cannot be proven. | Golden-file tests built from the rows in section 4. |

## 4. Edge cases to test

| # | Input rows (account_id, amount, currency) | What it exposes | Legacy result (verified or by reasoning) |
|---|---|---|---|
| 1 | `A1,,CAD` | Blank amount | Skipped. If it is the only row for `A1`, the account is absent from the output. |
| 2 | `A1, ,CAD` (space) | Whitespace-only amount | Crash with `ValueError`. |
| 3 | `A1,0.70,USD` | Rounding tie after conversion | 0.94 (half-up would give 0.95). Also try 1.70, 1.90, 2.30, 4.30 and 5.10 USD. |
| 4 | `A1,2.675,CAD` | Total formatting | `2.67`. |
| 5 | `A1,1.005,CAD` | Total formatting | `1.00`. |
| 6 | `A1,0.125,CAD` followed by `A2,0.375,CAD` | True binary ties | `0.12` and `0.38` (half-even). |
| 7 | `A1,0.1,CAD` ×10 | Float accumulation | Total is `1.00`, but the internal sum is a float. Test with larger volumes. |
| 8 | `A1,-0.001,CAD` | Negative zero | `-0.00`. |
| 9 | `A1,100,USD` and `A1,-100,USD` | Refunds and zero total | `0.00`, `txn_count` 2. |
| 10 | `A1,0,CAD` | Zero amount | Counted in `txn_count`. |
| 11 | `A1,10,usd` and `A1,10,USD ` | Currency case and padding | Both treated as CAD and not converted. |
| 12 | `A1,10,EUR`, `A1,10,` and `A1,10,CAD` | Unknown or empty currency | All treated as CAD. |
| 13 | `A1,1,000.50,USD` (unquoted) | Thousands separator, which also shifts columns | Extra field, so the amount parses as 1. Whether `"1,000.50"` quoted is allowed is UNKNOWN. In the quoted form it crashes with `ValueError`. |
| 14 | `A1,1e2,CAD` and `A1,1_000,CAD` | Exotic numeric forms | Accepted as 100 and 1000. |
| 15 | `A1,nan,CAD` and `A1,inf,CAD` | Non-finite values | Output `nan` and `inf` as text. |
| 16 | `a1`, `A1`, ` A1`, `A1 ` | Account ID case and whitespace | Four separate accounts. |
| 17 | `,5,CAD` (blank account) | Blank key | A valid account with an empty ID, sorted first. |
| 18 | Accounts `2`, `10`, `B1`, `a1` | Sort order | `10`, `2`, `B1`, `a1`. |
| 19 | Header-only file, and a zero-byte file | Empty input | Header-only output, exit 0. |
| 20 | Header missing `currency`, or renamed (`Amount`) | Schema drift | `KeyError`. |
| 21 | File with UTF-8 BOM | Encoding | First header becomes `\ufeffaccount_id`, so `KeyError`. |
| 22 | A row with fewer fields than the header | Short row | `amount` is `None`, so `TypeError`. |
| 23 | Duplicate transaction rows (identical lines) | Dedup rule | Both counted. The legacy job has no dedup, and whether it should is UNKNOWN. |
| 24 | Many USD rows with sub-cent products, for example 1000 × `0.01,USD` | Per-row versus aggregate rounding | Per row: 0.01 × 1.35 rounds to 0.01 each, so total 10.00 where aggregate rounding gives 13.50. |
| 25 | Input path equal to output path | Overwrite | Works, because the input is fully read first. The output replaces the input. |

Open questions for the business (all UNKNOWN from the source): the intended rounding mode, whether FX is per row or per total, the valid currency list, the treatment of blank amounts and account IDs, whether duplicates should be removed, the intended sort order, and the expected encoding.