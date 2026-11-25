I'll run a few of the rounding and parsing cases first so the expected outputs in the edge-case table are verified rather than guessed.

Line numbers below count from the top of `legacy_etl.py` as pasted, starting at the docstring. Rounding, parsing and sort behaviour was checked by running the cases. The raw rounding output is in the edge-case table.

## 1. BUSINESS RULES

1. **Columns used.** The job reads only `account_id`, `amount` and `currency` from a CSV with a header row. Any other columns are ignored, and whether the real file has others is UNKNOWN.
2. **Blank amounts.** A row is skipped only if `amount` is exactly an empty string. It is not counted, not logged, and not written to a reject file.
3. **Other bad amounts.** There is no validation. `float()` accepts `" 5 "`, `1e3`, `1_000`, `nan` and `inf`. Anything it can't parse (`" "`, `1,000`, `$5`) raises `ValueError` and aborts the whole run.
4. **Missing fields.** A short row leaves `amount` as `None`, which raises `TypeError`. A missing column raises `KeyError`. Either way the run aborts.
5. **Currency conversion.** `currency` must equal `"USD"` exactly (case-sensitive) to be converted. Every other value, including `CAD`, blank, `usd` and `EUR`, is silently treated as CAD with no conversion. Whether other currencies are valid in the real data is UNKNOWN.
6. **FX rate.** USD amounts are multiplied by a fixed 1.35 with no effective date. Whether 1.35 is the intended business rate is UNKNOWN.
7. **Rounding.** Each USD transaction is rounded to 2 decimals with Python `round()` on a binary float, before summing. CAD amounts are not rounded per transaction.
8. **Grouping.** Rows are grouped by `account_id` as an exact string, with no trimming or case folding. A blank `account_id` is a valid group `""`.
9. **Counting.** `txn_count` counts every non-skipped row, including zero and negative amounts and duplicate rows.
10. **Negatives and zeros.** Both are accepted and summed. Refunds and reversals net against sales with no flag.
11. **Total.** The total is a plain float sum, formatted with `"%.2f"` at the end. This is a second rounding step.
12. **Ordering.** Output rows are sorted by `account_id` as a string (codepoint order), so `"10"` sorts before `"2"`.
13. **Accounts with no valid rows.** An account whose rows were all skipped does not appear in the output. An empty input produces a header-only file.
14. **No rejects.** There is no reject file, no error log and no reconciliation of rows read against rows written. Whether the business needs one is UNKNOWN.
15. **All-or-nothing output.** The output file is opened only after all input is processed, so a crash leaves no partial output. An existing output file is not touched on a crash, but it is overwritten without warning on success.
16. **Dates.** The source has no date column and no date logic, so "bad date" rules do not apply to this code. Whether the real input has a date column is UNKNOWN.

## 2. INPUTS AND OUTPUTS

**Input: CSV file at `argv[1]`**

| Column | Type / format | Used how |
|---|---|---|
| `account_id` | string, format UNKNOWN | Group key, exact match |
| `amount` | decimal text parsed as float; `""` allowed | Summed; blank rows skipped |
| `currency` | string; only `"USD"` is meaningful | Selects conversion |
| other columns | UNKNOWN | Ignored |

- **Format:** comma-delimited, `"` as quote character, header required.
- **Encoding:** the default locale encoding (no `encoding=` argument), so the real encoding is UNKNOWN. A UTF-8 BOM would corrupt the first header name and raise `KeyError`.

**Input: command-line arguments.** `argv[1]` is the input path and `argv[2]` is the output path. With fewer than two arguments the script fails with `IndexError`.

**Output: CSV file at `argv[2]`**

| Column | Type / format |
|---|---|
| `account_id` | string, as read |
| `txn_count` | integer, no padding |
| `total_cad` | string from `"%.2f"`: dot decimal, 2 decimals, no thousands separator, may be `-0.00` |

- **Format:** comma-delimited and minimally quoted. The line terminator is `\r\n` (`csv.writer` default). Encoding is the default locale encoding.
- **Console output:** none, except Python tracebacks on failure.

## 3. TECHNICAL DEBT REGISTER

| Issue | Where | Risk | Suggested fix |
|---|---|---|---|
| Binary float used for money | L13, L16, L23 | Penny drift in totals that depends on row order and size | Use `Decimal` or integer cents end to end |
| `round()` on a float after FX | L15 | Ties are inconsistent: USD 0.10 gives 0.14 but USD 100.10 gives 135.13 | Use `Decimal` with an explicit rounding mode, agreed with the business (UNKNOWN today) |
| Hardcoded FX rate | L7 | Wrong totals when the rate changes, and no audit trail of which rate was used | Take the rate from config or a dated rate table; log it |
| Blank amounts skipped silently | L11–12 | Lost transactions with no trace | Write them to a reject file with a reason; report counts |
| No validation of amount format | L13 | A single bad value aborts the whole job; `nan` and `inf` would poison totals | Validate and reject per row; also reject non-finite values |
| Non-USD currencies treated as CAD | L14 | EUR, GBP or typo'd `usd` are added unconverted | Allow-list currencies, normalise case, and reject unknown values |
| No `account_id` normalisation | L16–17 | `"A1"`, `"a1"` and `"A1 "` become separate accounts; blank account is accepted | Trim, define case rules, reject blanks |
| No duplicate handling | L10–17 | Re-delivered rows are double counted | Define a business key and a dedupe or reject rule (UNKNOWN) |
| No handling of missing columns or short rows | L10–13 | `KeyError` or `TypeError` crash with no context | Check the header up front and fail with a clear message |
| String sort of account IDs | L22 | `"10"` before `"2"`; downstream consumers may expect another order | Make the ordering an explicit, documented rule |
| Default encoding | L9, L19 | The same file behaves differently across machines; BOM breaks the header | Set `encoding` explicitly (e.g. `utf-8-sig` on read) |
| No logging, row counts or reconciliation | whole file | Problems can't be detected after the fact | Log rows read, skipped, rejected and written |
| Module-level script with no functions or tests | L5–23 | Hard to test, reuse or change safely | Wrap in `main()` with functions; add golden-file tests |
| Positional `sys.argv` and no usage check | L9, L19 | Cryptic `IndexError`; the output path can overwrite any file | Use `argparse` and refuse to overwrite without a flag |
| `-0.00` possible in output | L23 | Downstream parsers or reports may show a negative zero | Normalise zero in the formatter (business decision UNKNOWN) |
| `\r\n` line endings on all platforms | L19–20 | Diffs and consumers differ on Linux versus Windows | Make the line terminator explicit and match the downstream expectation (UNKNOWN) |

## 4. EDGE CASES TO TEST

Expected values are what the legacy code produces, verified by running it. Compare a rewrite against these.

| # | Input rows (`account_id, amount, currency`) | Legacy result | What it exposes |
|---|---|---|---|
| 1 | `A, "", CAD` | Row skipped, `A` absent from output | Blank-amount rule |
| 2 | `A, "", CAD` and `A, 5.00, CAD` | `A, 1, 5.00` | Skipped rows are not counted |
| 3 | `A, " ", CAD` | Crash (`ValueError`) | Whitespace-only is not treated as blank |
| 4 | `A, " 5 ", CAD` | Accepted as 5.0 | Whitespace tolerance |
| 5 | `A, 1_000, CAD` and `A, 1e3, CAD` | Both accepted as 1000.0 | Lenient parsing |
| 6 | `A, nan, CAD` and `A, inf, CAD` | Accepted; total becomes `nan` or `inf` | Non-finite values |
| 7 | `A, 1,000, CAD` and `A, $5, CAD` | Crash (`ValueError`) | Formatted numbers are not supported |
| 8 | `A, 0.10, USD` | 0.135 rounds to **0.14** | Rounding tie, rounds up |
| 9 | `A, 100.10, USD` | 135.135 rounds to **135.13** | Rounding tie, rounds down; inconsistent with row 8 |
| 10 | `A, 1.10, USD` | 1.4850000000000003 rounds to 1.49 | Float noise above a tie |
| 11 | `A, 0.01, USD` | 0.0135 rounds to 0.01 | Small-amount rounding |
| 12 | `A, -0.10, USD` | −0.135 rounds to −0.14 | Negative tie rounding |
| 13 | `A, 0.125, CAD` (single row) | Output `0.12` (exact tie, half-even) | Final `%.2f` rounding |
| 14 | `A, 2.675, CAD` (single row) | Output `2.67` | Float representation of a decimal tie |
| 15 | `A, -0.001, CAD` (single row) | Output `-0.00` | Negative zero |
| 16 | Ten rows of `A, 0.10, CAD` | Float sum is exactly `1.0` here, so no visible drift; test with large mixed data | Order-dependent float sums |
| 17 | `A, 5, usd` and `A, 5, EUR` and `A, 5, ""` | All treated as CAD, no conversion | Currency handling |
| 18 | `A, 10.00, USD` | 13.50 | Normal conversion |
| 19 | `A, 0, CAD` | Counted; `A, 1, 0.00` | Zero amounts count |
| 20 | `A, 5, CAD` and `A, -5, CAD` | `A, 2, 0.00` | Refunds net out |
| 21 | Two identical rows | Both counted and summed | No dedupe |
| 22 | `"", 5, CAD` | Account `""` appears as a row | Blank account ID |
| 23 | `A1`, `a1`, `A1 ` (trailing space) | Three separate accounts | No normalisation |
| 24 | Accounts `10`, `2`, `01`, `A1`, `a1` | Order: `01, 10, 2, A1, a1` | String sort order |
| 25 | Header only, or empty file | Header-only output; a zero-byte input gives header-only output | Empty input |
| 26 | Missing `currency` column, or a short row | `KeyError` or `TypeError` crash | Schema errors |
| 27 | File starting with a UTF-8 BOM | `KeyError` on `account_id` if read with a non-BOM encoding | Encoding |
| 28 | Existing output file at `argv[2]` | Overwritten on success; untouched on crash | Overwrite behaviour |
| 29 | Large file (millions of rows) | Streams rows; memory scales with the number of accounts | Performance baseline (UNKNOWN whether it is a concern) |
| 30 | Date-like columns or bad dates | Not used by this code | UNKNOWN whether the real input has dates |

**Open questions for the business (all UNKNOWN):**
- Which rounding rule is intended: half-up or half-even?
- Is 1.35 the correct rate, and should it vary by date?
- Which currencies are valid?
- Should blank, bad and duplicate rows be rejected, and where should rejects go?
- What is the required `account_id` format and sort order?
- What encoding and line ending does the downstream consumer expect?