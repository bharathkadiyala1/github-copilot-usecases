## Source_Target_Mapping

| Map ID | Source System | Source Schema | Source Table | Source Column | Source Data Type | Target Schema | Target Table | Target Column | Target Data Type | Key | Transformation / Business Logic | Nullable | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M01 | CSV file read from sys.argv[1] | | Input CSV (path from sys.argv[1]; file name UNKNOWN) | account_id | UNKNOWN | | Output CSV (path from sys.argv[2]; file name UNKNOWN) | account_id | UNKNOWN | PK | Group key, copied as-is with no trim or case change. Output rows are sorted ascending as strings: sorted(totals). | N | Key is implied only by grouping (one output row per distinct account_id). No key is declared in the code. An empty-string account_id is accepted as a group. |
| M02 | CSV file read from sys.argv[1] | | Input CSV (path from sys.argv[1]; file name UNKNOWN) | account_id | UNKNOWN | | Output CSV (path from sys.argv[2]; file name UNKNOWN) | txn_count | UNKNOWN | | Count of non-skipped rows per account_id: counts[account_id] = counts.get(account_id, 0) + 1. Rows with a blank amount are not counted. | N | Written as a plain integer. Zero, negative and duplicate rows are all counted. Other columns used for the skip test: amount. |
| M03 | CSV file read from sys.argv[1] | | Input CSV (path from sys.argv[1]; file name UNKNOWN) | amount | UNKNOWN | | Output CSV (path from sys.argv[2]; file name UNKNOWN) | total_cad | UNKNOWN | | Per row: amt = float(amount). If currency = 'USD' then amt = round(amt * RATE, 2), with RATE hardcoded as 1.35; else amt unchanged, so any other currency value is treated as CAD. Then sum per account_id: totals[account_id] = totals.get(account_id, 0.0) + amt. Written as the string "%.2f" % totals[account_id]. | N | Other columns used: currency, account_id. Float arithmetic, with rounding applied per USD row and again at output. Can output -0.00. The hardcoded rate has no effective date. |

## Joins_Filters

| Seq | Type | Rule Name | Left Table (Alias) | Right Table (Alias) | Join Type | Join Condition / Filter Expression | Business Logic / Rationale | Applies To Map IDs |
|---|---|---|---|---|---|---|---|---|
| 1 | Driver | Read input rows | Input CSV (f) | - | - | for row in csv.DictReader(f) | Reads every data row of the input file, using the header row as column names. No joins exist in the code. | All |
| 2 | Filter | Skip blank amounts | Input CSV (row) | - | - | if row["amount"] == "": continue | Rows with an empty amount are dropped silently. They are not counted, not rejected and not logged. A whitespace-only amount is not dropped and raises an error. | All |
| 3 | Aggregation | Sum and count per account | Input CSV (row) | - | - | totals[row["account_id"]] = totals.get(row["account_id"], 0.0) + amt; counts[row["account_id"]] = counts.get(row["account_id"], 0) + 1 | Equivalent to GROUP BY account_id with COUNT and SUM of the CAD-converted amount. USD amounts are converted first (see M03). | M01, M02, M03 |
| 4 | Target Load | Write output CSV | Totals and counts dictionaries (totals, counts) | Output CSV (w) | - | for acct in sorted(totals): w.writerow([acct, counts[acct], "%.2f" % totals[acct]]) | Writes the header account_id, txn_count, total_cad, then one row per account in ascending string order. The file at sys.argv[2] is overwritten. | All |

## Assumptions and open questions

- The code names no database, schema or table. "Table" here means the CSV files passed as sys.argv[1] (input) and sys.argv[2] (output). Their real file names are UNKNOWN, and both schema columns are blank for that reason.
- No data types are declared. All CSV fields are read as text and then converted in code, so source and target data types are UNKNOWN. Values behave as follows:
  - amount is parsed with float().
  - txn_count is a Python integer.
  - total_cad is a text string with 2 decimals.
- Source System is described as a CSV file because that is all the code shows. The upstream system that produces the file is UNKNOWN.
- The only columns the code reads are account_id, amount and currency. Whether the input file has other columns is UNKNOWN.
- The PK on account_id is inferred from the grouping. No uniqueness constraint is declared.
- Nullable is Y or N as the code behaves: the output values are never null, so N. No nullability is declared in any schema.
- The code treats USD as the only foreign currency. Whether other currencies such as EUR can occur is UNKNOWN. Any value other than exactly "USD" is added as if it were CAD.
- The hardcoded rate 1.35 has no source or effective date. Whether it is the correct business rate is UNKNOWN.
- The rounding rule intended by the business is UNKNOWN. The code uses Python round() on floats, which gives inconsistent results on ties.
- Dedup: the code has no dedup, so none is listed. Whether duplicate rows are valid or should be removed is UNKNOWN.
- File encoding and the output line terminator are not set in the code. The output uses the csv module default of CRLF. The encoding expected downstream is UNKNOWN.
- Errors such as an unparseable amount or a missing column abort the whole run. No reject path exists in the code.