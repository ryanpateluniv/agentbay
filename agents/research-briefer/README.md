# Research Briefer

Gives a one-page research brief with five findings and a watch list, then answers one follow-up. No live web access.

## Run locally

```bash
export MARKETPLACE_SHARED_SECRET=changeme OPENAI_API_KEY=...   # key stays in a local .env, never committed
python main.py                                  # serves on :8081
LLM_MODE=fake python tests/test_server.py       # signature + session tests, no key needed
```

## Numbers

| | |
|---|---|
| Price | 10 credits (per message) |
| Metering | 10 credits per message, up to 3 messages per session (about 30 credits) |

## Notes

- Test cases: `tests/cases.jsonl`. Fixtures in `tests/files/`.
- Samples for the listing page: `samples/`.
