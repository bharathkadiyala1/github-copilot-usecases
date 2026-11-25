# Legacy Modernization Workbench

Shows how to move a legacy ETL job to a modern, config-driven pipeline **and prove nothing changed**,
using GitHub Copilot for the analysis and conversion work.

- **Pure Python 3.9 - 3.11, standard library only.** No `pip install`, no internet, no cloud access.
- **All settings live in `.env`.** Open it, change a value, run again.
- **Five legacy systems, one modern pipeline, one parity gate.**

## Quick start (Windows PowerShell or any terminal)

```
py -3 modernize.py all --fake-copilot
```

(Use `python` instead of `py -3` on Mac/Linux.) This creates mock data, "asks" a pretend Copilot,
runs the modern pipeline for the system in `.env`, compares it with the legacy output, and writes
`reports/modernization_report.md`. Add `--all-systems` to do all five.

Run the tests: `py -3 -m unittest discover -s tests -v`

## What happens, step by step

| Command | What it does |
|---|---|
| `setup` | Makes mock transactions (fixed-width and CSV) and the golden (expected) outputs. |
| `analyze` | Builds Copilot prompts from the legacy source: business rules + technical debt list. |
| `convert` | Prompts for unit tests and a PySpark conversion, then runs the modern pipeline. |
| `parity` | Compares modern output with legacy output. **Exits with an error on any mismatch.** |
| `report` | Writes `reports/modernization_report.md` (parity table + Copilot audit trail). |
| `all` | Does all of the above. |
| `demo-break` | Changes the rounding on purpose to show the parity gate catching it. |
| `health` | Shows the last run status of the modern pipeline. |

Options: `--system python\|sql\|cobol\|sas\|ssis`, `--all-systems`, `--fake-copilot`.

## The five legacy systems

All five implement the same banking rules (see `docs/business_rules.md`) on the same mock data.

| System | Source file | Can run here? | Quirk |
|---|---|---|---|
| python | `legacy/python/legacy_etl.py` | Yes | Hardcoded paths, one big loop, rounds half up |
| sql | `legacy/sql/legacy_etl.sql` | Yes (built-in `sqlite3`) | Chain of temp tables, integer cents, CSV input |
| cobol | `legacy/cobol/TXNSUM.cbl` | No, reference only | `COMPUTE` without `ROUNDED` truncates |
| sas | `legacy/sas/txn_summary.sas` | No, reference only | `ROUND()` half away from zero |
| ssis | `legacy/ssis/txn_summary_package.xml` | No, reference only | `Math.Round` is banker's rounding (half even) |

**Honest note:** for cobol, sas and ssis the expected outputs in `golden/` are **simulated** by
`tools/simulate_legacy.py`. In real work you capture the real output files from the legacy system and
drop them into `golden/<system>/`. python and sql golden files come from running the real legacy code.

## Using GitHub Copilot

`copilot/client.py` supports two modes, chosen by `COPILOT_CMD` in `.env`:

1. **Paste mode (leave `COPILOT_CMD` blank).** The tool writes each prompt to `generated/prompts/`.
   Paste it into Copilot Chat in VS Code, save the reply into `generated/copilot_answers/` with the
   same file name, and run the command again. It will find the answer.
2. **Command mode.** Set `COPILOT_CMD` to whatever command your workplace allows. `{prompt_file}` is
   replaced by the prompt file path, `{prompt}` by the prompt text; with neither, the prompt goes in on stdin.
   Example: `COPILOT_CMD=your-copilot-command {prompt_file}`. I could not test a real Copilot command from here;
   `tools/fake_copilot.py` is only a pretend one so the flow can be demonstrated offline.

Every prompt is logged in `audit/prompt_log.csv` with `REVIEWER` from `.env` and a `human_decision`
column. Fill that column in after you review each answer: it is your responsible-AI evidence.

`.github/copilot-instructions.md` tells Copilot the house rules for this repo. Copy it into your work repo.
Edit the prompt wording any time in `copilot/prompts/*.txt`.

## Delta Lake / Iceberg (PySpark)

Set `TARGET=delta` or `TARGET=iceberg` and run `convert`. The tool writes `generated/<system>_<target>_job.py`.
It is **only run** if `RUN_SPARK=true` and `pyspark` plus Java are already installed. Delta needs the
`delta-spark` package, Iceberg needs its runtime jar (put the path in `SPARK_JARS`). Without Spark you still get
code to review, and the standard-library pipeline does the real proof. The generated PySpark was only
syntax-checked here, not run, so test it on your Spark before trusting it.

## Folder map

```
.env                  all settings            modernize.py   the command line
modern/               the new pipeline: config, extract, transform, load, pipeline, spark_codegen
legacy/               the five legacy systems (python and sql run; others are reference)
parity/               compare + report        copilot/       prompts + Copilot client
tools/                mock data, golden files, simulator, fake Copilot
tests/                unittest suite          docs/          business rules
data/ golden/ output/ generated/ audit/ reports/    created when you run it
```

## Make it your own

- **New rule:** edit `modern/transform.py` (one small function per rule) and `docs/business_rules.md`.
- **New legacy system:** put its source in `legacy/<name>/`, add it to `SOURCES` in `copilot/prompts.py`,
  to `SYSTEMS` in `modern/config.py`, add its rounding/input format to `.env`, and drop its real output into `golden/<name>/`.
- **Bigger data:** set `MOCK_ROWS=1000000`. The modern pipeline streams rows, so memory depends on the number
  of accounts and distinct transaction ids, not the file size.
- **Real data:** do not. Keep this repo on mock data only.
