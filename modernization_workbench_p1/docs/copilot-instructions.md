# Copilot instructions for this repository

This project modernizes legacy ETL jobs and proves behaviour is unchanged. Follow these rules.

## Code style
- Python 3.9 to 3.11, **standard library only**. Do not add pip dependencies.
- Keep code short and readable: small functions, clear names, a comment for anything non-obvious.
- No hardcoded settings. Paths, rates, fees, rounding and switches come from `.env` through `modern/config.py`.
- Money is whole cents (integers). Never use floats for money.

## Safety
- Mock data only. Never put real client data, credentials, tokens or internal URLs in code, prompts or tests.
- No network calls. Do not import cloud SDKs at the top of a file.
- Validate every input row; reject it with a reason code instead of crashing.

## Behaviour
- The business rules are in `docs/business_rules.md`. If a change alters a rule, update that file and the tests.
- Every change must keep `python modernize.py parity --all-systems` passing.
- Use `unittest` for tests (not pytest). Run `python -m unittest discover -s tests`.

## When asked to convert a legacy job
1. List the business rules first, and any hidden quirks (rounding, date handling, null handling).
2. Write characterization tests before changing behaviour.
3. Keep output files byte-for-byte identical to the legacy output unless a human approves a difference.
4. Add logging, and keep the code modular: extract, transform, load.
