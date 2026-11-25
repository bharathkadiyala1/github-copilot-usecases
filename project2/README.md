# Parity Workbench

A small local web page that helps you move a legacy data job (SAS, COBOL, SQL, Python, anything) to a
modern Python job **and proves the results match**, with Copilot doing the drafting and a human approving.

- Python 3.9+, **standard library only**. No install, no internet, no cloud access.
- The page only listens on your own computer (`127.0.0.1`).
- All settings are in `.env` (copy `.env.example`).
- The tool contains **no business rules**. It compares files. Your jobs live in `jobs/<name>/`.

## Run

```
python app.py          # opens http://127.0.0.1:8000
python -m unittest discover -s tests -v
```

## The page, top to bottom

1. **Legacy job.** Add the legacy script, the input CSV, and the legacy output CSV (from the SAS/COBOL team, or from a run).
   If the legacy script can run here (Python, SQL, shell) set its command and click *Run legacy script* to create the legacy output.
2. **Ask Copilot.** Pick a step, *Build prompt*, copy it into Copilot Chat, paste the reply back, *Save reply*.
   Steps: explain rules + tech debt, generate sample input (script only), write the new Python job, cloud skeleton (AWS/Azure/...).
   Optional: set `COPILOT_CMD` in `.env` to get an *Ask Copilot* button (untested here, I had no Copilot command to try).
3. **New job.** Paste the Python code. You must review and click **Approve** before it can run. Editing the code removes the approval.
4. **Parity check.** Compares legacy output with the new output. PASS, or FAIL with the exact rows/columns that differ.
5. **Approve + report.** Record the decision, download `*_parity_report.md`. Every action is in `audit/audit_log.csv`.

## Script only, no input/output?

The tool cannot prove parity without the legacy output. With only a script it can still explain it, draft the new job, and
ask Copilot for **sample input**. If the script runs here the tool creates the legacy output; if not (SAS/COBOL), someone runs it once
on the sample input and you upload the output. Results on generated input are labelled **"sample data"**.

## Demo jobs (mock data)

| Job | Shows |
|---|---|
| `demo_python_pass` | Legacy Python run here; new job reproduces it. PASS. |
| `demo_python_fail` | Same legacy; a typical exact-Decimal rewrite. FAIL: legacy float `round()` gives 0.94 where exact maths gives 0.95. |
| `demo_sas_capture` | SAS source plus a captured output. **That output is simulated** (no SAS here); real use means a real SAS export. |

Try each: pick the job, click *Approve* in step 3, *Run legacy script* (python jobs), *Run new job*, *Run parity check*.

## Your own job

*Create job*, then follow the steps. Options per job: key columns (match rows by key, order ignored), tolerance for numbers,
and the legacy/new command templates using `{python}`, `{input}`, `{output}`.

## Limits (honest)

- Compares CSV files. Other formats: have the job export CSV.
- Key mode loads both files in memory; row-by-row mode streams.
- Cloud skeletons (Glue, Databricks, ADF) are generated for review, never run.
- Approved code runs on your machine as you; review it first. The page blocks other websites from calling it.
- A pass only covers the data you compared.
