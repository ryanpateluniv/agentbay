# No-code agents

A no-code agent is a folder with three files and no Python:

```
my-agent/
  agent.yaml    manifest: listing, pricing, limits, and the `nocode:` runner settings
  prompt.md     the instructions sent to the model
  cases.jsonl   test cases the review runs
```

Start from `agents/_template/` (a Meeting Notes agent). To publish it you can either:
- **Put it in the repo.** Copy it to `agents/<name>/`. On startup the server registers it and reviews it, and if it's approved it goes live.
- **Upload it as a creator.** Zip the folder and upload it, or send the three files as JSON (see "Publishing" below). An admin approves it before it's listed.

The platform runs the folder with `marketplace_sdk.nocode`. Every model call goes through the platform proxy, so it's metered, capped and charged like any other agent.

## How a session runs

1. Each entry in `nocode.questions` is asked in order.
2. `prompt.md` is filled in and sent to the model.
3. The model can reply with one line, `ASK: question | option | option`, to ask the buyer something. It can reply `CONFIRM: what I will do` to ask for approval. Otherwise it returns the final Markdown answer. These round trips count toward `max_turns`.
4. If `confirm` is set, the buyer is asked it.
   - Approved: the answer is saved to `output_file`.
   - Declined: the answer is still shown, but no file is saved.
5. The answer is returned as the result.

## agent.yaml fields

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Slug: lowercase letters, digits and `-`. Unique on the marketplace. |
| `display_name`, `summary` | yes | The listing title and a one-line summary (10–300 characters). |
| `description`, `category`, `tags`, `example_tasks` | no | Shown on the listing. The first `example_tasks` entry is also used as the hidden review case. |
| `version` | yes | `x.y.z`. Bump it for every new submission; the same version can't be submitted twice. |
| `runtime.kind` | yes | `nocode`. No `entrypoint` is needed. |
| `models_allowed` | yes | `fast` and/or `balanced`. Other aliases are rejected. |
| `nocode.model` | no | The alias used for the calls. Defaults to the first entry in `models_allowed`. |
| `nocode.prompt_file` | no | Defaults to `prompt.md`. |
| `nocode.questions` | no | Up to 5 entries of `{id, question, options?}`. Without `options` the buyer types a free-text answer. |
| `nocode.confirm` | no | The approval question asked before `output_file` is saved. |
| `nocode.output_file` | no | For example `result.md`. The answer is also delivered as this file. |
| `nocode.max_turns` | no | 1–6, default 3. The total number of model calls, including ASK and CONFIRM round trips. |
| `nocode.max_tokens` | no | 100–4000, default 1200. The output cap per call. |
| `nocode.allow_questions` | no | Default true. Set false to stop the model from using ASK and CONFIRM. |
| `limits.max_session_minutes`, `limits.max_llm_cost_credits` | yes | Hard limits per session. |
| `pricing` | yes | See "Pricing" below. |
| `support_contact` | yes | An email address. |
| `tools_requested` | no | Must be empty (no-code agents can't use tools yet). |

## prompt.md

The prompt is plain Markdown with these placeholders:

| Placeholder | Filled with |
| --- | --- |
| `{{task}}` | The buyer's task. If the prompt doesn't use it, the task is sent as the user message instead. |
| `{{answers.<id>}}` | The buyer's answer to the question with that `id`. If no answer placeholder is used, the answers are listed after the task. |
| `{{files}}` | The text of any files the buyer attached (up to 20,000 characters in total). |

Say what to do when the input is missing or off-topic, for example: "ask for the notes instead of guessing". The runner adds the ASK and CONFIRM rules for you.

## cases.jsonl

Write one JSON object per line, up to 9 lines. The review runs each case twice, plus one hidden case built from `example_tasks[0]`.

```json
{"id": "basic", "task": "Ana will send the budget by Friday...", "answers": ["My team", "yes"], "expect_contains": ["Ana"]}
```

- `answers` are replayed in the order the agent asks for them: first the fixed questions, then any ASK or CONFIRM from the model, then `confirm`. For approvals, `"no"`, `"deny"` or `"false"` means decline and anything else means approve. If the answers run out, the review answers "Yes, please proceed."
- `expect_contains` lists words that must appear in the result or the saved file, ignoring case. Use names or facts taken from the task, so a correct answer is bound to contain them.
- Include at least one case where the buyer declines the save, and one with messy input.

## Pricing

- `per_task` with `price_credits` is the simplest pricing for no-code agents. The buyer sees one fixed price.
- The review measures your model cost (p50 and p90) and rejects a flat price below `ceil(cost_p90 / (1 - platform_fee))`. The report card shows this as `suggested_min_price_credits`.
- To estimate your cost: one `fast` call with about 1,000 tokens in and 800 out costs well under 1 credit. Set `max_llm_cost_credits` to about 3 times your p90 cost, so long inputs don't hit the cap.
- `usage_based` with `markup_pct` suits agents whose inputs vary a lot in size. The buyer pays the measured cost plus your markup.

## Publishing (API)

All publishing calls use `Authorization: Bearer <token>` from an account with the creator role (`POST /v1/me/creator` switches it on).

| Call | What it does |
| --- | --- |
| `POST /v1/creator/template-agents` | Sends `{manifest, prompt, cases}` as JSON. Creates the agent, or adds a version to one you own, as a draft. |
| `POST /v1/creator/template-agents/upload` | Same, but the body is the zip itself (`Content-Type: application/zip`, up to 1 MB). |
| `POST /v1/creator/agents/{agent_id}/versions/{version_id}/submit` | Starts the review. |
| `GET /v1/creator/agents/{agent_id}/versions/{version_id}/review` | Returns the review stage, report card, findings and status. |

A version that passes review waits for an admin. Approval makes it live and listed. Rejection keeps it hidden and adds the admin's reason to the findings.
