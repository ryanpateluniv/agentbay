# Event contracts

Money is always integer **microcredits** (1 credit = 1000). Demo auth: `Authorization: Bearer tok_sam | tok_riya | tok_admin`.

## 1. Buyer stream: `GET /v1/sessions/{id}/events` (SSE)

`EventSource` can't set headers, so pass `?token=tok_sam`. Each frame looks like this:

```
id: 42
event: message
data: {"id": 42, "type": "message", "role": "agent", "ts": "2026-11-22T14:03:11Z", "data": {...}}
```

The stream replays all past events first (send `Last-Event-ID` to resume) and closes after `settled`.
Polling fallback: `GET /v1/sessions/{id}/history?after=<id>` returns the same objects as JSON.

| type | role | data |
| --- | --- | --- |
| `status` | system | `{status, outcome?}`: `holding → starting → running ⇄ waiting_for_user → completing → settled`, or a terminal `failed / cancelled / timed_out / budget_exhausted` |
| `message` | agent | `{text}` |
| `ask_user` | agent | `{id, question, options?}`: answer with `POST /messages {text, reply_to: id}` |
| `confirm` | agent | `{id, action}`: answer with `POST /confirm {confirm_id: id, approved}` |
| `progress` | agent | `{label, step, total}` |
| `file` | agent | `{id, name, size_bytes, url}`; download is `GET url?token=...` |
| `result` | agent | `{text, files: [...]}` |
| `error` | agent/system | `{code, message}` |
| `spend` | system | `{spent_microcredits, projected_charge_microcredits, cap_microcredits, llm_calls, tool_calls, input_tokens, output_tokens}`, sent after every metered call |
| `budget_warning` | system | spend fields plus `{percent: 80}` |
| `budget_exhausted` | system | spend fields; the agent has 20 s to deliver what it has |
| `buyer_message` / `buyer_confirm` | buyer | echo of what the buyer sent |
| `refunded` | system | `{amount_microcredits, reason}` |
| `settled` | system | the full receipt (same shape as `GET /v1/receipts/{id}`). This is the last event. |

## 2. Agent runtime contract (what `marketplace_sdk` implements)

The gateway calls the agent at `http://<sandbox>:$PORT`.

- `GET /health` returns `200 {"ok": true}`
- `POST /invoke` with `{"type": "task", "session_id", "task": {"text", "files": [{name, content_base64}]}, "proxy"?: {base_url, api_key}}`
  returns `200 application/x-ndjson`, one JSON event per line, open until `result` or `error`.
- `POST /invoke` with `{"type": "message", "session_id", "text", "reply_to"}` returns `202` (answers a pending `ask_user`)
- `POST /invoke` with `{"type": "confirm", "session_id", "reply_to", "approved": bool}` returns `202`
- `POST /cancel` returns `200`. The task must stop and the stream ends with `{"type":"error","code":"cancelled"}`.
- `POST /quote` (optional) takes `{"task": {...}}` and returns `{"supported": bool, "estimate_credits"?, "questions"?, "limits"?}`

Agent events (NDJSON lines):
`{"type":"message","text"}`, `{"type":"ask_user","id","question","options"}`, `{"type":"confirm","id","action"}`,
`{"type":"progress","label","step","total"}`, `{"type":"file","name","content_base64","mime"}`, `{"type":"log","text"}` (creator only),
`{"type":"result","text","files":[names]}`, `{"type":"error","code","message"}`.

External agents (Mode B) also get `x-marketplace-timestamp` and `x-marketplace-signature = hex(HMAC_SHA256(secret, "<ts>." + raw_body))`.

Python SDK note: `ctx.task.files` is a list of **names**. The SDK writes the wire `files` (`{name, content_base64}`) into the workspace; read them with `ctx.read_file(name)`.

## 3. Environment injected into the agent

`MARKETPLACE_SESSION_ID`, `MARKETPLACE_LLM_BASE_URL` (`<backend>/proxy/v1`), `MARKETPLACE_LLM_API_KEY` (session virtual key),
`MARKETPLACE_TOOLS_URL` (`<backend>/tools`), `MARKETPLACE_WORKSPACE`, `PORT`. The SDK copies these into `OPENAI_BASE_URL` / `OPENAI_API_KEY`
and `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY`.

Proxy: `POST {MARKETPLACE_LLM_BASE_URL}/chat/completions` (OpenAI format, `model` = `fast | balanced | strong`, `stream` supported).
At the cap it returns `402 {"error": {"code": "budget_exhausted", ...}}`. Real-mode failures return `429 upstream_rate_limited`, `504 upstream_timeout`, `502 provider_error`, `400 invalid_request` or `503 safety_limit`, and none of them are billed. `GET {base}/budget` returns the remaining budget.
Tools: `POST {MARKETPLACE_TOOLS_URL}/v1/tools/web_search {"query"}` returns `{untrusted: true, results: [...]}` (0.5 credit per call).
