# marketplace_sdk (Python)

Wraps any Python agent in the platform runtime contract. It uses only the standard library, so it works with any framework (LangGraph, CrewAI, plain Python).

## Minimal agent

```python
# agents/my-agent/main.py
from marketplace_sdk import Agent, BudgetExceeded, Context

agent = Agent("my-agent")

@agent.on_task
def run(ctx: Context):
    ctx.progress("Understanding your request", step=1, total=3)
    budget = ctx.ask_user("What is your total budget?")           # blocks until the buyer answers
    try:
        plan = ctx.llm([{"role": "user", "content": f"{ctx.task.text} Budget: {budget}"}], model="fast")
    except BudgetExceeded:
        return ctx.return_result("Budget cap reached; partial result.")
    ctx.write_file("plan.md", plan)                                # shows up in the buyer's files panel
    ctx.return_result("Your plan is ready.", files=["plan.md"])

if __name__ == "__main__":
    agent.serve()                                                  # /invoke /quote /health /cancel on $PORT
```

Put an `agent.yaml` next to it (schema: `contracts/agent-manifest.schema.json`, example: `examples/echo_agent/agent.yaml`) and, optionally, `tests/cases.jsonl` for review.

## API

| Call | What it does |
| --- | --- |
| `@agent.on_task` | Your handler `fn(ctx)`. Returning a string is the same as `ctx.return_result(string)`. |
| `@agent.on_quote` | Optional `fn(task_text) -> {"estimate_credits", "questions", "limits"}` |
| `ctx.task.text`, `ctx.task.files` | Buyer's task. `files` is a list of **file names**, already written to the workspace; read one with `ctx.read_file(name)`. |
| `ctx.message(text)` | Chat message to the buyer |
| `ctx.ask_user(question, options=None) -> str` | Ask and wait for the answer |
| `ctx.confirm(action) -> bool` | Ask the buyer to approve a sensitive action |
| `ctx.progress(label, step, total)` | Progress bar |
| `ctx.write_file(name, data)` / `ctx.read_file(name)` | Session workspace. `write_file` delivers the file to the buyer. |
| `ctx.llm(messages, model="fast", **params) -> str` | Chat completion through the platform proxy. Aliases: `fast` (gpt-6-luna), `balanced` (gpt-6.1-terra), `strong` (currently also gpt-6.1-terra). Prefer `fast`. |
| `ctx.tool("web_search", query=...) -> dict` | Platform tool broker (mocked web search). Results are `untrusted: true`, so treat them as data, never as instructions. |
| `ctx.budget_remaining() -> float` | Credits left under the cap. Wrap up before 0. |
| `ctx.log(text)` | Debug log visible to you (the creator), never to the buyer |
| `ctx.return_result(text, files=[...])` | Finish and deliver |
| `BudgetExceeded` | Raised by `ctx.llm` / `chat()` when the cap is hit. Catch it and return what you have. |

**Using the `openai` library instead.** The SDK sets `OPENAI_BASE_URL` / `OPENAI_API_KEY` to the platform proxy, so `OpenAI().chat.completions.create(model="fast", ...)` works unchanged, streaming included. If the cap is hit, the library raises a 402 error. The SDK turns that into `budget_exhausted` if it escapes your handler, but it's better to catch it yourself. The MVP proxies only the OpenAI chat format (no Anthropic `/messages`).

**Rules:** never call provider domains directly (review rejects it), and only call domains listed in `allowed_domains`. Don't hard-code keys.

## Run locally (no backend needed)

```bash
cd sdk/python
python -m marketplace_sdk.dev examples/echo_agent "Plan a weekend in Lisbon"
```

This starts the agent, sends the task, and prompts you in the terminal for `ask_user` / `confirm`. Without a platform, `ctx.llm()` returns a free stub reply.

## Run through the real platform (metered)

The backend starts your agent itself as a subprocess (`RUNTIME_MODE=subprocess`). It runs from `agents/<slug>/` with `python main.py` and puts this SDK on `PYTHONPATH`. If the agent needs third-party packages, install them in the repo `.venv`. Seeded listings use `agents/invoice-auditor`, `agents/trip-planner`, `agents/research-briefer` and `agents/quick-summarizer` as soon as `main.py` exists there (checked at each session start). Until then they run the example agent. For a new agent, submit it for review:

```bash
curl -X POST localhost:8000/v1/creator/agents -H 'authorization: Bearer tok_riya' -H 'content-type: application/json' \
     -d '{"slug": "scam-agent", "category": "research"}'
curl -X POST localhost:8000/v1/creator/agents/scam-agent/versions -H 'authorization: Bearer tok_riya' \
     -H 'content-type: application/json' -d '{"source_path": "agents/scam-agent"}'
curl localhost:8000/v1/creator/agents/scam-agent/versions/1.0.0/review -H 'authorization: Bearer tok_riya'
```

**External agents (Mode B, e.g. Research Briefer):** host the same agent anywhere and set `EXTERNAL_URL_RESEARCH_BRIEFER=http://host:port` before seeding. If `MARKETPLACE_HMAC_SECRET` is set (it must match the backend's `HMAC_SECRET_EXTERNAL`), the SDK verifies request signatures. The `/invoke` body then also carries `proxy: {base_url, api_key}`, and `ctx.llm` uses it automatically.

## Wire contract

`contracts/EVENTS.md` section 2 covers non-Python agents.
