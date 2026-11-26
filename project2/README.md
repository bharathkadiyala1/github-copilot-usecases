# BMO ParityPilot

*AI-assisted modernization, with proof.* Copilot drafts, the tool measures and checks, a named person signs off, and every step is written to an audit trail.

- Python 3.9+, **standard library only**. No installs, no internet, no cloud access.
- A small local web app. It only listens on your own computer (`127.0.0.1`).
- Settings live in `.env` (copy `.env.example`). Brand colours and fonts live at the top of `ui/style.css`.
- Mock data only.

## Run

```
python3 app.py          # opens http://127.0.0.1:8000   (on Windows: python app.py)
python3 -m unittest discover -s tests -v
```
Restart `app.py` after changing any `.py` file. Changes to files in `ui/` only need a browser refresh.

## Pages

| Page | What it does |
|---|---|
| **Modernization Studio** `/parity` (core) | The end-to-end workflow for one legacy job: attach the old script, input data and legacy output, generate the new code with Copilot, run it on your machine, compare the results (PASS or FAIL with the exact differing cells), sign off. The new code must be signed off before it runs. |
| **Code Insights** `/understand` | Attach a script. Instantly get size, tables read and written, joins, CASE rules, hardcoded values and risky patterns (High / Medium / Low), plus a plain-English summary. Then ask Copilot for a business-friendly explanation and read it nicely. |
| **Mapping from Code** `/code-to-mapping` | Copilot rebuilds a source-to-target mapping from code. The tool reads the two tables it returns, checks them, checks they match the code, keeps **versions** as the code changes, and exports **Excel (.xlsx)** in the same layout as the example mapping template (plus CSVs). |
| **Code from Mapping** `/mapping-to-code` | Upload a mapping workbook (.xlsx). It checks the document for gaps, keeps **versions** and shows what changed, then builds a prompt for **SQL** (pick a dialect) or **PySpark**. Paste Copilot's reply; the code is extracted and a **rule coverage** check shows which columns, joins, filters and parameters appear in it. |
| **Reconcile** `/reconcile` | Compare any two CSV files (for example source vs target after a migration). No migration needed. |
| **Evidence report** `/report?job=NAME` | One page per migration with everything it has: verdict, sign-offs, script analysis, explanation, mapping and versions, generated code, audit trail. Print or save as PDF, or download the markdown. |
| **Home** `/` | Hub with the core tile first, the supporting tools, your migrations, and ideas for other use cases. |

A **migration** is one folder under `jobs/` that holds a modernization's files, Copilot replies, versions and sign-offs. Pages share migrations, so work carries from one page to the next.

## Mapping workbook format (Mapping → Code)

Any header names, column order, tab names and tab order are accepted. Nothing is matched by exact text.
- **Formats:** `.xlsx`, or CSV / tab-separated / markdown / JSON tables. The same reader is used for Copilot's reply on the Code → Mapping page.
- **Tables are found by score, not by name.** Every sheet and every row is tried as a header row. The mapping table and the joins/filters table can be on separate tabs (any names, any order) or stacked on one sheet. Title rows above the header, two-row headers (a "Source" band over "Table" / "Column") and merged cells are handled.
- **Columns are recognised three ways:** by the header words ("Src Tbl", "Target Field Name", `sourceColumn`, typos), by the values in the column (IDs like `M01`, SQL types, `PK`/`FK`, `INNER`/`LEFT`, `a.x = b.y`), and by position when two columns tie (source before target). A "Source ..." header is never read as a "Target ..." one.
- **Minimum needed:** a target column plus a source column or a transformation rule. A joins/filters tab is optional (the page warns that joins and filters will be missing). Everything else may be absent.
- **Values are normalised:** "Base Table" becomes Driver, "WHERE" becomes Filter, "Left Outer Join" becomes LEFT, "Primary Key" becomes PK, "NOT NULL" becomes N, `1.0` becomes `1`. Columns it does not use are kept as extras, never thrown away.
- **Nothing is guessed silently.** Every column gets a confidence (high / medium / low). If the file is not clearly readable, it is **not saved**; the page shows **Check how the file was read**: each column with a dropdown to say what it means, and an optional Copilot step. The Copilot prompt contains only the header rows and a few sample rows and Copilot only says which column is which. The tool applies the answer itself, so no row can be changed, dropped or invented. Confirmed choices are remembered for the migration, so the same layout needs no review next time.
- Reference SQL pasted in a cell is detected and checked against the rules too.

`samples/Source_Target_Mapping_Example.xlsx` is an example. In it, the workbook's own reference SQL covers only some of its rules (it is missing the dedup, the valid-currency filter, the posted-only filter and the group by). The page shows this.

## What is checked by tools vs by AI

| Done by the workbook (no AI) | Done by Copilot (reviewed by a person) |
|---|---|
| Script statistics and risk patterns | Plain-English explanation |
| Reading and writing Excel mappings | Mapping tables from code |
| Gap checks on the mapping document | SQL / PySpark from a mapping |
| Coverage of mapping items in code | Starter code for AWS / Azure (never run) |
| Output comparison, preview, checksums, audit trail | Sample input and predicted legacy output (labelled UNVERIFIED) |

## Using Copilot

Default is copy and paste: build the prompt, copy it into Copilot Chat, paste the reply back, save. Nothing is sent anywhere by this app.
Optional: set `COPILOT_CMD` in `.env` to show an *Ask Copilot directly* button (untested here, I had no Copilot command to try).

## Unit tests and documentation (Mapping → Code)

Each SQL or PySpark prompt now also asks Copilot for a **pytest file** under a "## Unit tests" heading: one test per Map ID and per join / filter / dedup / aggregation rule, with tiny mock rows and exact expected values. SQL tests use DuckDB and PySpark tests use a local SparkSession; a test the engine cannot run is kept and skipped with a reason.
- The tool saves the code and the tests as separate files, with the mapping version they belong to, and checks that the test file is valid Python, calls the code, and that **every mapped column and rule has a test that asserts something**. Tests that assert nothing (`assert True`), are skipped, or are missing are listed.
- **It does not prove the expected values are right.** It proves the tests exist, parse and cover every item. Run them (`pip install pytest duckdb`, then `pytest test_generated_sql.py` in the folder holding both files) and have a person check the expected values.
- **Documentation** is written by the tool (not by Copilot) from the mapping, the checks, coverage, tests and the change list, so it cannot disagree with them. Only the "Assumptions and open questions" section is Copilot's, marked *AI draft*.
- The page's **Summary** card shows the code, test coverage and documentation, and downloads one **package** (code, tests, `documentation.md`, `mapping.xlsx`). Each version's package can be downloaded from the version table. The evidence report includes the test results and the documentation.

## Mapping versions

Upload the mapping again after the business changes it. The workbook saves it as the next version, keeps the old code and Copilot reply with the version they belong to, and compares the two mappings. Spacing-only edits are ignored. Changes are labelled **Logic change**, **Technical** (types, nullable) or **Notes only**. For code, tick *Update the previous version's code*: the prompt then includes the old code and the change list, so Copilot edits what changed instead of rewriting everything, and the code diff stays small. A hint table shows whether the code diff touched each changed item.

The **Code → Mapping** page versions too. Each time you rebuild the mapping after the code changed, it is saved as a new version with a snapshot of the script. The page shows what changed in the mapping and, line by line, what changed in the code (and says so when the code changed but the mapping did not). Any version can be downloaded as Excel or CSV.

Columns are matched by target column name (then by Map ID, so a renamed column is one change, not an add plus a remove). Rules are matched by rule name, then by Seq.

## Honest limits

- The "did the code change where the mapping changed?" table is a text hint: it looks for the changed names in the changed lines. It is not proof either way.

- **Script statistics are pattern matching, not a parser.** Counts are approximate. Best for SQL, Python and SAS.
- **Coverage is a text check.** It looks for names and expressions in the code. It does not run it. "Found" means present, not correct.
- Generated SQL and PySpark are **not executed** (no database or Spark here). Validate them in your own environment, then compare results with the Prove or Reconcile pages.
- A mapping document has no answer key, so the workbook can show coverage and gaps but cannot prove the logic is right.
- Mapping upload supports `.xlsx` only (not PDF or Word). Excel files written by this app were checked by reading them back, not opened in Excel.
- The Prove page runs the new Python job on your machine. Review it first. Approval is tied to the file's checksum, so editing the code removes it.
- A pass only covers the data that was compared.

## Logo

Save the logo into `ui/` as `logo.png` (or `logo.jpg` / `logo.svg`). It appears in every page header. 

## Files

```
app.py          web server and page routes            core.py       jobs, prompts, compare, audit trail
workflows.py    Understand / mapping job actions      mapping.py    read and write .xlsx, check mappings, coverage
scan.py         script statistics and risk patterns   prompts/      Copilot prompt templates (edit freely)
schema.py       resilient header / layout reader      qa.py         unit-test checks and documentation builder
ui/             pages, style.css, shared md.js nav.js common.js
jobs/           one folder per migration (demo jobs included)     samples/   example mapping workbook
tests/          unit and end-to-end tests             audit/        audit_log.csv (created when you run it)
```
