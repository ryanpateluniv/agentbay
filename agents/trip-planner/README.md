# Trip Planner

Plans a multi-day trip through a short chat: you pick from three outlines and approve the final itinerary.

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
| Price | 120 credits (per task) |
| p50 / p90 target | 25 / 45 credits |
| Measured p50 / p90 | _TBD - run `marketplace test` with the real runtime_ |

## Notes

- Test cases: `tests/cases.jsonl`. Fixtures in `tests/files/`.
- Samples for the listing page: `samples/`.
