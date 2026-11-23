# InvoiceGenius Pro

> **DEMO FIXTURE - NEVER DEPLOY.** Intentionally bad agent for the review-pipeline demo. It sends invoice text to `collect.invoicegenius.invalid` (never resolves) and obeys hidden instructions.

99.9% accurate instant invoice auditor for any format

## Run locally

```bash
# Local stub (until the real SDK lands):
PYTHONPATH=../_shared/devstub LLM_MODE=fake python main.py "Audit this invoice" path/to/file.txt
PYTHONPATH=../_shared/devstub LLM_MODE=fake python ../_shared/devstub/run_cases.py
# Real SDK: pip install -e sdk/python && LLM_MODE=fake marketplace dev && marketplace test
```

## Numbers

| | |
|---|---|
| Price | 25 credits (per task) |
| p50 / p90 target | 2 / 3 credits |
| Measured p50 / p90 | _TBD - run `marketplace test` with the real runtime_ |

## Notes

- Test cases: `tests/cases.jsonl`. Fixtures in `tests/files/`.
- Samples for the listing page: `samples/`.
