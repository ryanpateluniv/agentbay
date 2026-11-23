# Agent Marketplace — Full Platform Blueprint

Nov 22, 2026 · @Ryan

## 1. How to use this document

Paste this whole document into any LLM prompt as the project spec; for a narrow task (say, billing), paste Sections 1 to 4 plus the section that applies.

- "Must" marks a rule. "Recommended" marks a default that can change. "MVP" marks what we build in the 12-hour hackathon.
- Terms are defined in the glossary at the end (Section 16). Assumptions we have not confirmed are listed there too.
- If a later decision conflicts with this text, the later decision wins; update this document.

Suggested prompt preamble:

```text
You are a senior engineer on the project described below. Treat the document as the product spec. Use its terminology and rules. If my request conflicts with the spec, tell me. If the spec is silent, state your assumption before answering.

[paste the blueprint here]
```

### Executive summary

1. The Marketplace is an app store for AI agents: creators publish agents, and buyers search, compare, and use them.
2. An agent is a full program with its own multi-step workflow, built in any framework and packaged as a repo or container with our SDK.
3. The platform runs each agent in a sandbox. Buyers never talk to an agent directly; every message passes through our gateway.
4. Every model call goes through our LLM proxy using our provider keys and a per-session virtual token, so usage is exact and cannot be faked.
5. Buyers pay in platform credits (assumed 1 credit = $0.01), bought with money or through a subscription.
6. Each session reserves the buyer's budget, meters usage live, then settles and refunds what was not used.
7. A buyer's payment splits into model and tool cost, platform fee, and creator earnings.
8. Every agent passes automated review (tests, security checks, cost audit) and gets a report card and a confidence score before it is listed.
9. Buyers describe a task, get a shortlist, and compare price range, confidence, speed, and ratings side by side.
10. Safety comes from sandboxing, scoped tokens, spending caps, hidden tests, reputation, and a kill switch.

## 2. Product vision and core concept

The Marketplace is an app store for task-completing AI agents: creators build and sell agents, buyers pay per use in credits, and the platform sits in the middle to verify, run, meter, and bill every interaction.

### The problem

- **Buyers** face thousands of agents and tools. They cannot tell which ones work, what a task will really cost, or whether an agent is safe with their data.
- **Creators** can build capable agents but have no distribution, no billing system, and no way to earn buyer trust.

### The solution

A single marketplace where every agent is reviewed, tested, and scored before listing, runs inside our infrastructure, and is billed through one credit system. Buyers get comparable evidence (price range, confidence, speed, ratings). Creators get customers, billing, and payouts without building any of it.

### The app-store analogy

| App stores | The Marketplace |
| --- | --- |
| Apps | Agents (full programs with their own workflows) |
| App review | Automated review, report card, and confidence score |
| In-app purchases and subscriptions | Credits, top-ups, and subscription plans |
| Developer payouts | Creator earnings and payouts |
| App sandbox and permissions | Agent sandbox, scoped tokens, and egress allowlist |
| OS services (push, payments) | Platform services (LLM proxy, tool broker, file storage) |

### Design principles

1. **The platform sits in the middle.** Buyers never talk to an agent directly. Every message, model call, and tool call passes through our gateway. This is what makes tracking and billing possible.
2. **The platform owns the meters.** Usage is measured by us, never self-reported by creators.
3. **Agents are black boxes.** We do not need to understand an agent's internal workflow. We control its door (the runtime contract) and its two phone lines (the LLM proxy and tool broker).
4. **Trust comes from evidence.** Scores are computed by the platform from tests and real usage, and always show their sample size.
5. **Cost must be predictable.** Buyers see a quote before starting and set a spending cap that the platform enforces.
6. **Any framework is welcome.** LangGraph, CrewAI, plain Python, or anything else works, as long as the agent follows the contract.

### What the Marketplace is not

- It is not a model provider and does not train models.
- It does not show creators' source code to buyers.
- It is not a no-code agent builder in the MVP (a template mode may come later).

## 3. Actors and roles

There are three human roles and two system actors; one account can hold both the buyer and creator roles.

| Actor | Who they are | What they can do | Money |
| --- | --- | --- | --- |
| Buyer | A person or team that wants a task done | Search by task description, compare agents, set a budget cap, run sessions, chat with the agent, download results, rate, dispute | Pays credits; buys credits with money or a subscription |
| Creator | A developer or team that builds agents | Build with the SDK, submit agents, read review reports, set price, publish versions, view analytics, request payouts | Earns a share of each session's price |
| Reviewer or admin | Platform staff plus the automated review system | Run and audit reviews, approve, reject, or delist agents, handle disputes, set model prices and fee rules, trigger the kill switch | Platform revenue |
| Agent (system actor) | A running container for one session | Receive tasks, call the LLM proxy and tool broker with its session token, message the buyer, return results | None directly |
| Platform (system actor) | Gateway, proxy, metering, ledger, scheduler | Authenticate, route, meter, hold and settle credits, enforce limits | Holds funds in escrow during sessions |

### Visibility rules

- Buyers never see a creator's source code or prompts.
- Creators see aggregate analytics (sessions, success rate, revenue, costs, ratings, failure categories), but not buyer identities or private session content.
- Reviewers see everything needed for review and disputes, with access logged in the audit trail.
- An agent only sees data the buyer sends inside the current session. It cannot read other sessions or other buyers' data.

## 4. System architecture

The platform sits between every buyer and every agent, so each message, model call, and tool call can be authorized, metered, and billed.

&#91;embedded content: architecture · 7 platform parts, 2 outside services\]

The agent reaches models and tools only through the LLM proxy and the tool broker, and both write usage events to metering; the gateway holds credits when a session starts and triggers settlement when it ends.

### Text version of the diagram

```text
Buyer web app ----> Gateway ----> Agent sandbox (creator's agent)
Creator portal ---> Registry + Review pipeline ---> Gateway
Agent sandbox ----> Tool broker ---+
Agent sandbox ----> LLM proxy -----+--> Metering --> Ledger <---- Payment provider
LLM proxy --------> Model providers (real provider keys stay on our servers)
Gateway ----------> Ledger (hold credits at start, settle at end)
```

### Components

| Component | Responsibility |
| --- | --- |
| Buyer web app | Search, compare, chat with agents, wallet, receipts |
| Creator portal and CLI | Submit versions, edit listing and price, analytics, earnings, payouts |
| Gateway | Authentication, quotes, session creation, budget caps, rate limits, routing messages and events (server-sent events), triggering hold and settle |
| Registry | Agents, versions, manifests, listings, scores, badges |
| Review pipeline | Build, scan, sandbox tests, report card (Section 8) |
| Agent runtime and sandbox | Starts one container per session, injects the environment, enforces limits, destroys the container |
| LLM proxy | Virtual keys, model allowlist, token counting, cost, cap enforcement |
| Tool broker | Platform tools with prices and limits |
| Egress proxy | Allows only approved outbound destinations and counts requests |
| Metering service | Ingests usage events, keeps running session totals, produces rollups |
| Ledger and billing | Wallets, hold, settle, split, refunds, payouts |
| Search and quote service | Embeddings, re-ranking, price estimator |
| Storage | Built images, session files, event logs |
| Admin console | Review queue, disputes, kill switch, prices and fees |

### Data flow of one session

1. The buyer picks an agent. The gateway creates a quote, then a session with a budget cap, and the ledger holds that amount.
2. The runtime starts a container and injects the session environment (virtual key, proxy and tool addresses, creator secrets).
3. The gateway sends the buyer's task to the agent's `/invoke`.
4. The agent works. Each model call goes to the LLM proxy and each tool call to the tool broker; each writes a usage event, metering updates the running total, and the gateway streams spend to the buyer.
5. The agent sends messages, questions, and progress through the SDK; the gateway relays them to the buyer.
6. The agent returns a result; outputs are stored and the container is destroyed.
7. The gateway asks the ledger to settle: compute P, C, F, and E, post the entries, and release the unused hold.
8. The buyer gets a receipt and a rating window; the creator's earnings update.

## 5. Agent integration in detail

A creator's agent becomes part of the platform by being packaged as a program that speaks one standard contract; the platform runs it in a sandbox and controls its only two ways to reach models and tools.

### The mental model

The agent is a black box. Its internal workflow (steps, loops, sub-agents, frameworks) is the creator's business. We control three things: the **door** (how tasks and messages get in and out), the **LLM line** (every model call goes through our proxy), and the **tools line** (every platform tool goes through our broker). Because everything crosses those three boundaries, we can meter and bill without understanding the workflow.

### Integration modes

| Mode | How it works | Metering | Status |
| --- | --- | --- | --- |
| A. Hosted container (primary) | Creator submits a repo or image with the SDK. We build it and run it in our sandbox. | Exact: all model and tool calls pass through our proxy and broker | MVP |
| B. External endpoint | Creator hosts the agent on their own server. We call it over HTTPS using the same contract. | Partial: we see requests, duration, and output size, not their internal model usage. Price per task, message, or minute. | MVP (one demo agent) |
| C. Template (no-code) | Creator fills in prompt, model alias, tools, and knowledge files; we run a generic runtime. | Exact | Later |

### What a creator submits

A repository (or Docker image) containing:

- The agent source code, in any language, using any framework.
- A `Dockerfile` or a declared `entrypoint` we can build with a standard buildpack.
- An `agent.yaml` manifest (below).
- The Marketplace SDK as a dependency (Python first; JavaScript next; any language can implement the raw HTTP contract).
- Optional test cases (`tests/cases.jsonl`) that the review pipeline will run in addition to our own tests.
- A README with usage notes, limitations, and example tasks.

### The manifest (`agent.yaml`)

```yaml
name: trip-planner
display_name: Trip Planner
version: 1.2.0
summary: Plans multi-city trips with flights, hotels, and a day-by-day itinerary.
description: |
  Longer text shown on the listing page.
category: travel
tags: [travel, planning, itinerary]
example_tasks:
  - "Plan a 5-day trip to Tokyo under $2,000"
inputs: [text, pdf]
outputs: [markdown, pdf]
runtime:
  language: python
  entrypoint: "python main.py"
  port: 8080
tools_requested: [web_search, file_storage]
models_allowed: [fast, strong]   # platform aliases, mapped to real models
limits:
  max_session_minutes: 20
  max_llm_cost_credits: 200
  max_tool_calls: 100
pricing:
  model: per_task                # per_task | per_message | per_minute | usage_based | outcome_based | hybrid
  price_credits: 150
secrets: [SERPAPI_KEY]           # creator's own third-party keys, stored encrypted
review:
  test_cases: tests/cases.jsonl
support_contact: creator@example.com
```

Model aliases (`fast`, `balanced`, `strong`) are defined by the platform and mapped to real provider models, so we can change providers or prices without breaking agents.

### Environment the agent receives

| Variable | Meaning |
| --- | --- |
| `MARKETPLACE_SESSION_ID` | Identifier of the current session |
| `MARKETPLACE_LLM_BASE_URL` and `MARKETPLACE_LLM_API_KEY` | Our proxy address and a session-scoped virtual key |
| `OPENAI_BASE_URL` and `OPENAI_API_KEY` | Set by the SDK to the same values, so most OpenAI-compatible libraries work with no code change |
| `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` | Same idea for libraries that read these |
| `MARKETPLACE_TOOLS_URL` | Address of the tool broker |
| Creator secrets | Decrypted into the environment at start (for example `SERPAPI_KEY`) |

The agent never receives a real provider key.

### The SDK

| Function | Purpose |
| --- | --- |
| `@agent.on_task` | Marks the function that handles a new task |
| `ctx.message(text)` | Send a chat message to the buyer |
| `ctx.ask_user(question, options=None)` | Ask the buyer something and wait for the answer |
| `ctx.confirm(action)` | Ask the buyer to approve a sensitive action before doing it |
| `ctx.progress(label, step, total)` | Update the progress bar shown to the buyer |
| `ctx.read_file(name)` and `ctx.write_file(name, data)` | Use the session workspace |
| `ctx.budget_remaining()` | Credits left under the buyer's cap |
| `ctx.return_result(text, files)` | Finish the task and deliver outputs |
| `ctx.log(text)` | Write a debug log visible to the creator, not the buyer |
| `agent.serve()` | Starts the HTTP server that exposes the runtime contract |

Example:

```python
from marketplace_sdk import Agent, Context
from openai import OpenAI          # SDK has already set OPENAI_BASE_URL / OPENAI_API_KEY

agent = Agent()

@agent.on_task
def run(ctx: Context):
    ctx.progress("Understanding your request", step=1, total=4)
    budget = ctx.ask_user("What is your total budget?")
    client = OpenAI()               # calls go through the platform proxy automatically
    plan = client.chat.completions.create(
        model="fast",
        messages=[{"role": "user", "content": ctx.task.text + f" Budget: {budget}"}],
    ).choices[0].message.content
    ctx.write_file("itinerary.md", plan)
    ctx.return_result(text="Your itinerary is ready.", files=["itinerary.md"])

agent.serve()                       # exposes /invoke, /quote, /health
```

### The runtime contract (HTTP)

| Route | Called by | Purpose |
| --- | --- | --- |
| `POST /invoke` | Gateway | Start a task or deliver a buyer message; response is an event stream |
| `POST /quote` | Gateway | Optional: estimate cost and ask clarifying questions before a session |
| `GET /health` | Gateway | Liveness check |
| `POST /cancel` | Gateway | Stop the running task immediately |

The event stream uses these event types: `message`, `ask_user`, `confirm`, `progress`, `file`, `result`, and `error`. The platform adds its own `usage` events from the proxy and broker. The SDK implements all of this, so most creators only write the handler function.

### Build and deployment

1. Creator pushes the repo (or an image) and submits it.
2. The platform builds an image, scans it, and stores it in a private registry.
3. The review pipeline (Section 8) runs the image in an isolated sandbox.
4. On approval, the version is marked live; the image stays immutable.
5. For each buyer session the platform starts a fresh container from that image, injects the environment above, and destroys the container when the session ends.

### Session lifecycle

1. `created`: buyer chose an agent and a budget cap.
2. `holding`: platform reserves credits (Section 7).
3. `starting`: container boots, health check passes.
4. `running`: the agent works; usage events stream in.
5. `waiting_for_user`: the agent asked a question or needs confirmation (the time limit is paused; the cost meter does not run).
6. `completing`: the agent returned a result; outputs are saved.
7. `settled`: charges computed, credits released, earnings recorded.
8. Terminal exceptions: `failed`, `cancelled`, `timed_out`, `budget_exhausted`; each has a refund rule (Section 7).

### Resource limits per session

| Limit | Recommended default |
| --- | --- |
| Wall-clock time | 20 minutes (agent can request more in the manifest, up to a platform maximum) |
| Model spend | The buyer's budget cap, and never above the manifest limit |
| Tool calls | 100 |
| CPU and memory | 1 vCPU and 1 GB (higher tiers priced into the agent) |
| Disk workspace | 500 MB, deleted after the retention period |
| Network | Egress allowlist only (Section 14) |

### State and memory

Agents are stateless across sessions by default. Anything an agent needs to remember within a session lives in its workspace. Cross-session memory is a later feature, would go through a platform memory API, and requires buyer consent.

### External endpoint mode (Mode B)

- The creator hosts an HTTPS service that implements the same routes.
- Every platform request is signed (HMAC with a shared secret and a timestamp); the agent must verify it.
- Because we cannot see inside, pricing must be per task, per message, or per minute, using what the gateway measures.
- The platform may hand the agent a session proxy key in each `/invoke` call so it can voluntarily route model calls through our proxy.
- External agents carry an "External" label, are probed by live canary tasks, and rank lower until they build a track record.

### Local development

The SDK ships a CLI: `marketplace init` scaffolds a project, `marketplace dev` runs the agent locally against a stub proxy with free test tokens, `marketplace test` runs the creator's test cases, and `marketplace submit` uploads the version for review.

## 6. Metering and cost tracking

Every cost is measured by the platform at a boundary the agent must cross, tagged with the session, agent version, creator, and buyer, and converted to credits with one fixed formula.

### What is metered

| Meter | Measured at | Unit | Used for |
| --- | --- | --- | --- |
| Model input and output tokens | LLM proxy | Tokens per model | Cost of the session |
| Other model features (embeddings, images, speech) | LLM proxy | Per unit | Cost of the session |
| Platform tool calls (search, file storage, code execution) | Tool broker | Per call or per unit | Cost of the session |
| Compute time | Sandbox runtime | vCPU-seconds and GB-seconds | Cost of the session (small; may be bundled) |
| Egress traffic | Egress proxy | Requests and bytes | Cost and abuse detection |
| Gateway events | Gateway | Messages, steps, minutes, success flag | Per-message, per-minute, and outcome pricing |

### The LLM proxy

The proxy is an OpenAI-compatible server in front of the model providers. It holds the real provider keys; agents never see them.

1. When a session starts, the platform mints a **virtual key** bound to the session, agent version, buyer, spend cap, allowed model aliases, and an expiry time.
2. The agent calls the proxy with a standard request (the SDK has already set the base URL and key in the environment).
3. The proxy checks the key, the model alias, the remaining cap, and rate limits.
4. It forwards the request to the provider with the platform's real key and streams the answer back.
5. It reads token counts from the provider's usage data (if missing, it uses a tokenizer estimate and reconciles later).
6. It writes a usage event and updates the session's running total.
7. If the cap is reached, the next call is rejected with a `budget_exhausted` error, which the SDK raises as `BudgetExceeded`.

Provider errors are never billed, and a retried call is billed once. If a streaming connection drops, the buyer is billed for the tokens actually streamed.

### Usage event

Every metered action writes one append-only event:

```json
{
  "event_id": "evt_01",
  "ts": "2026-11-22T14:03:11Z",
  "session_id": "ses_123",
  "agent_version_id": "agv_45",
  "creator_id": "usr_riya",
  "buyer_id": "usr_sam",
  "type": "llm_call",
  "model_alias": "fast",
  "provider_model": "<mapped provider model id>",
  "input_tokens": 1820,
  "output_tokens": 412,
  "provider_cost_usd": 0.0031,
  "cost_microcredits": 400,
  "latency_ms": 1240,
  "status": "ok"
}
```

Other event types: `tool_call`, `compute`, `egress`, `message`, `step`, `session_start`, `session_end`.

### Cost formula

The platform's cost for one model call, in credits:

```latex
C_{call} = \frac{(t_{in} \cdot p_{in} + t_{out} \cdot p_{out}) \cdot (1 + m)}{v_{credit}}
```

- `t_in` and `t_out` are input and output tokens; `p_in` and `p_out` are the provider's prices per token for the mapped model.
- `m` is the platform's cost markup (recommended 0 to 10 percent; it covers failed calls and overhead).
- `v_credit` is the dollar value of one credit (assumed $0.01).

Amounts are stored as integer **microcredits** (1 credit = 1,000 microcredits) to avoid floating-point errors. Session cost C is the sum of all event costs.

### The tool broker

- Platform tools (web search, file storage, code execution, and so on) are called through `MARKETPLACE_TOOLS_URL` with the session token.
- Each tool has a published price and per-session call limits.
- Tool outputs that contain outside content (web pages, files) are marked as untrusted so agents can treat them as data, not instructions (Section 14).
- MVP: web search and file storage are real or mocked; other tools come later.

### Egress control and the creator's own services

- Containers have no open internet access. Outbound calls go through an egress proxy that allows only domains declared in the manifest and approved in review.
- If an agent calls its creator's own paid services (for example a flight data API with the creator's key), that cost is the creator's and must be covered by the agent's price. The platform can count requests and bytes, but cannot see the creator's bill.
- Direct calls to model provider domains are blocked, so an agent cannot bypass the proxy.

### Attribution and reporting

Every event carries four IDs: session, agent version, creator, and buyer. Rollups are computed per session, agent version, creator, buyer, and model. The buyer's chat shows credits spent so far against the cap, live.

### Budget enforcement

- A warning appears at 80 percent of the cap; a hard stop happens at 100 percent.
- The buyer can extend the cap mid-session (subject to balance).
- The proxy limits each call's maximum output so a single call cannot overshoot the cap by much. Any small overshoot is absorbed by the platform.
- The agent can read `ctx.budget_remaining()` and wrap up gracefully before the stop.

### Integrity and anti-cheating

- Creators never report usage; only platform-measured events count.
- Usage events are append-only with idempotency keys.
- Daily reconciliation compares our totals with provider invoices; a drift above 2 percent raises an alert.
- Anomaly detection kills runaway loops (for example, identical repeated calls) and flags agents whose cost per session jumps.
- Attempts to bypass the proxy are blocked by the egress rules and recorded as security findings.

## 7. Credits, pricing, and money

Buyers pay in platform credits; each session's payment splits into the platform's model and tool cost, a platform fee, and the creator's earnings, and every movement is recorded in a double-entry ledger.

### Credits

- A credit is the platform's internal currency. Assumed value: 1 credit = $0.01.
- Credits are prepaid. Buyers get them by buying packs, subscribing, or receiving promotions.
- Purchased credits do not expire for 12 months (assumption). Promotional and subscription credits expire (assumption: end of the following month).
- Buyers cannot withdraw credits as cash. Unused purchased credits are refundable within 14 days of purchase (assumption), minus payment fees.
- Real money enters through a payment provider (Stripe Checkout; test mode in the MVP). A payment webhook credits the buyer's wallet in the ledger.

### Top-up packs and plans (examples to tune)

| Option | Price | Credits | Notes |
| --- | --- | --- | --- |
| Free trial | $0 | 200 once | Lets new buyers try cheap agents |
| Pack S | $10 | 1,000 | No bonus |
| Pack M | $25 | 2,750 | 10 percent bonus |
| Pack L | $50 | 5,750 | 15 percent bonus |
| Plus plan | $19 per month | 2,200 per month | Higher spending caps |
| Pro plan | $49 per month | 6,000 per month | Priority queue, higher caps, team seats |

### Pricing models creators can choose

| Model | The buyer pays | Best for | Risk to manage |
| --- | --- | --- | --- |
| Per task | One flat price per completed session | Clear, bounded jobs | Creator loses margin if a session uses many tokens |
| Per message | Price times messages the agent sends | Conversational agents | Chatty agents inflate cost |
| Per minute | Rate times active minutes (time waiting for the buyer is free) | Long, variable jobs | Slow agents cost more |
| Usage-based | Platform cost times (1 + creator markup) | Highly variable jobs | Less predictable for the buyer |
| Outcome-based | Price only if the result passes a success check; otherwise nothing | Verifiable jobs | Needs a clear success definition |
| Hybrid | Small base price plus usage-based part | Mixed jobs | Needs clear display of both parts |

### Price guardrails

- Every agent must have a maximum session cap in its manifest.
- The minimum allowed price must cover the agent's measured 90th-percentile cost after the platform fee: price at least p90(C) divided by (1 minus f), where f is the fee rate.
- The review pipeline measures C and shows creators a suggested price.
- Price changes apply to new sessions only and show to buyers before they start.

### The money split

Definitions for one session, all in credits:

- **P**: the price charged to the buyer under the agent's pricing model.
- **C**: the platform's cost for that session (models, tools, compute), from Section 6.
- **F**: the platform fee, equal to f times P. Default f = 20 percent (assumption).
- **E**: creator earnings, equal to P minus F minus C, and never below zero.

In the default all-in model the creator's price includes model and tool cost, so C comes out of the creator's share. If E would be negative, the creator earns 0 and the platform absorbs the loss; agents that often run at a loss are flagged to raise their price or are paused.

| Example | P | C | F (20%) | E | Platform net |
| --- | --- | --- | --- | --- | --- |
| Per task, normal run | 150 | 40 | 30 | 80 | 30 |
| Per task, expensive run | 150 | 180 | 30 | 0 (floored) | -30 (absorbed) |
| Usage-based, markup 150% | 100 (40 x 2.5) | 40 | 20 | 40 | 20 |
| Outcome-based, task failed | 0 | 25 | 0 | 0 | -25 (absorbed) |

### Hold and settle

1. **Quote and cap.** The buyer sees the price range and sets a cap (default: the quoted 90th-percentile price).
2. **Hold.** At session start the platform moves the cap from the buyer's wallet into escrow. If the balance is too low, the session does not start.
3. **Run.** Usage events accumulate; the live meter shows spend against the cap.
4. **Settle.** At the end the platform computes P, C, F, and E, moves funds out of escrow, and releases the unused hold back to the buyer.
5. **Receipt.** The buyer gets an itemized receipt with the agent price and a usage summary.

### The ledger

The ledger is append-only, double-entry, keyed by idempotency keys, and stored as integer microcredits. Accounts: `buyer_wallet`, `session_escrow`, `creator_payable`, `platform_revenue`, `provider_cost` (what we owe model and tool providers), and `promo_liability`.

Journal for the first example (cap 200, P = 150, C = 40, F = 30, E = 80):

| Step | Debit | Credit | Credits |
| --- | --- | --- | --- |
| Hold at start | buyer\_wallet | session\_escrow | 200 |
| Settle: model and tool cost | session\_escrow | provider\_cost | 40 |
| Settle: platform fee | session\_escrow | platform\_revenue | 30 |
| Settle: creator earnings | session\_escrow | creator\_payable | 80 |
| Release unused hold | session\_escrow | buyer\_wallet | 50 |

The escrow account always returns to zero after settlement (40 + 30 + 80 + 50 = 200).

### Failures, cancellations, and disputes

| Case | Buyer is charged | Creator earns |
| --- | --- | --- |
| Agent fails to start or crashes before any useful output | Nothing | 0 |
| Agent errors or times out without a result | Nothing (full refund) | 0; platform absorbs C; the failure counts against the agent's score |
| Buyer cancels before the agent starts working | Nothing | 0 |
| Buyer cancels mid-task | Work done so far: per-minute and per-message models charge consumed units; per-task charges 50% of P (assumption) | Normal split on the amount charged |
| Budget cap reached with a partial result delivered | The cap | Normal split |
| Budget cap reached with no usable result | Nothing | 0 |
| Outcome-based task fails the success check | Nothing | 0 |
| Buyer dispute within 7 days | Decided by review: refund or rejection | If the agent was at fault, the refund is taken from the creator's earnings or held balance |

### Creator payouts

- Earnings accrue in `creator_payable` and become available after a 14-day clearing window (assumption), which also covers disputes and chargebacks.
- Minimum payout: $25 (assumption). Credits convert to dollars at the credit value.
- Payouts go through Stripe Connect or bank transfer. MVP: payouts are simulated.
- Tax forms, regional compliance, and currency conversion are out of scope for the MVP.

### Money abuse controls

- Self-dealing: creators buying their own agent to farm payouts is detected through shared devices, payment methods, and patterns, and earnings from flagged sessions are withheld.
- Chargebacks: a card chargeback reverses the credits and claws back related earnings still in the clearing window.
- Velocity limits and fraud scoring on new accounts and large top-ups.
- Promotional credits cannot be paid out and are spent first only on agents that opt in (assumption).

## 8. Review and verification pipeline

No agent is listed until it passes an automated, evidence-based review that produces a report card and a confidence score; the same checks keep running after launch.

### Submission

The creator submits a repo or image, the manifest, optional test cases, a category, and a price. Each version is reviewed separately. Resubmissions are rate-limited, and hidden test sets are never shown, so creators cannot tune an agent to the tests.

### Pipeline stages

| # | Stage | What it checks | If it fails |
| --- | --- | --- | --- |
| 1 | Intake and manifest lint | Required fields, valid pricing, limits, declared tools and domains, allowed model aliases | Rejected with a list of fixes |
| 2 | Build and static scan | Image builds; dependency vulnerabilities; hard-coded secrets; license problems; code that calls provider domains directly | Changes requested |
| 3 | Sandbox security tests | Escape attempts, undeclared network access, file access outside the workspace, prompt-injection probes in tool outputs, data-exfiltration probes | Any critical finding is an automatic rejection |
| 4 | Functional benchmark | Category test suite plus creator test cases, each run 5 times; automated checkers and an LLM judge with a rubric | Score below threshold: changes requested |
| 5 | Cost and latency audit | Measured cost per session (p50, p90) against the declared price and limits; latency (p50, p95); step counts | Price must be raised or limits changed |
| 6 | Reliability | Crash, timeout, and malformed-output rates; behavior on bad inputs and empty inputs | Below threshold: changes requested |
| 7 | Policy and content | Category rules; disallowed use cases; honest description and example tasks | Rejected or listing text fixed |
| 8 | Human review (sensitive categories) | A reviewer checks finance, health, legal, and children's categories | Held until a reviewer decides |
| 9 | Report card and decision | Compiles all results and decides | Approved, approved with conditions, changes requested, or rejected |

### The report card

Every agent version gets a report card, shown in part to buyers and in full to the creator:

- Scores: quality, reliability, safety, cost accuracy, and the confidence score.
- Measured cost per session (p50 and p90 in credits) and the recommended price.
- Latency (p50 and p95) and typical number of steps.
- Tools, domains, and permissions the agent uses.
- Example passes and failures (redacted).
- Security findings and their status.
- Reviewer notes and the decision.

### The confidence score

Confidence is computed by the platform and never self-reported. It starts from review results and shifts toward real-world results as sessions accumulate.

```latex
\text{Confidence} = 100 \cdot \left( 0.40\,Q^{*} + 0.20\,R^{*} + 0.20\,S + 0.10\,K + 0.10\,U \right), \qquad X^{*} = (1-\lambda)\,X_{review} + \lambda\,X_{live}, \quad \lambda = \frac{n}{n+30}
```

| Symbol | Meaning |
| --- | --- |
| Q | Quality: share of test cases passed (review) and share of sessions that ended well (live) |
| R | Reliability: one minus the failure rate (crashes, timeouts, invalid outputs) |
| S | Safety: pass rate on security and policy tests; any critical failure blocks listing outright |
| K | Cost accuracy: how well the declared price covers the measured p90 cost, and how stable cost is |
| U | User rating, scaled to 0 to 1 and shrunk toward the category average when there are few ratings |
| n | Number of real sessions; lambda is the weight given to live results |

The score is always displayed with its sample size, for example "92, based on 340 sessions", and ranking uses a conservative lower bound so that a score from 3 sessions never outranks one from 300. A "live success" session means it finished without a refund or dispute and the buyer accepted the result.

### Badges

- **Beta**: fewer than 30 real sessions.
- **Verified**: passed review and has at least 30 sessions with score 80 or higher.
- **Top-rated**: score 90 or higher over at least 200 sessions.
- **External**: runs on the creator's own server (Section 5, Mode B).

### Keeping honest after launch

- **Re-review** on every version change.
- **Canary sessions**: hidden test tasks are mixed into real traffic from synthetic buyer accounts, so an agent that behaves differently when it detects a test is caught.
- **Continuous monitoring** of failure rate, refund rate, cost per session, latency, and ratings.
- **Auto-delist or pause** when confidence drops 15 points in 7 days, the refund rate passes 10 percent, p90 cost runs 30 percent above the declared figure, canaries fail, or any critical security event occurs.
- **Kill switch**: admins can stop an agent version immediately; running sessions are cancelled and refunded.
- **Appeals**: creators can contest a decision and a human reviews it.

### MVP version

Manifest lint, a container build, running 5 to 10 test cases in a sandbox, a cost and latency measurement, a few prompt-injection probes, an LLM-judge score, and a generated report card page. Human review and live canaries are simulated or later.

## 9. Discovery, comparison, and quotes

A buyer describes a task in plain words and gets a ranked shortlist whose price ranges and confidence scores come from platform data, not from creator claims.

### Search

1. The buyer types the task and may attach a file.
2. The platform embeds the description and retrieves candidates by similarity against agent descriptions, tags, example tasks, and the categories where each agent scored well in review.
3. An LLM re-ranker reads the top candidates and drops poor matches (for example, an agent that cannot accept the buyer's file type).
4. Buyers can filter by category, input and output types, maximum price, maximum time, verified only, and data-handling policy.
5. Ranking combines relevance with quality-adjusted value: the conservative confidence bound, price fit, and speed. A small exploration slot shows Beta agents so new creators can earn data. There is no paid placement.

### What buyers compare

| Field | Where it comes from |
| --- | --- |
| Price range for this task (low to high, in credits) | Platform estimator |
| Pricing model | Manifest |
| Confidence score and number of sessions | Platform |
| Typical time to finish (p50 and p95) | Measured |
| Success rate on similar tasks | Live sessions |
| Ratings and number of reviews | Verified buyers only |
| What the agent needs from you (inputs, questions it will ask) | Manifest |
| Tools and data it can access | Manifest and review |
| Version and last review date | Registry |
| Badges (Beta, Verified, Top-rated, External) | Platform |
| Sample outputs | Creator, checked in review |

### Quotes and price estimates

- A **quote** is the platform's estimate of what this agent will charge for this buyer's task: a range from the typical (p50) to the high end (p90), plus a maximum.
- The platform finds past sessions on similar tasks for that agent version and takes the p50 and p90 of the billed price. New agents use the measurements from review. Flat-price agents quote exactly their price.
- An agent may also implement `POST /quote` to ask clarifying questions or state limits (for example, "cannot handle more than 10 pages"). Its own estimate is shown only if it is within 20 percent of the platform's; a much lower estimate is treated as under-quoting and the platform's number is shown.
- A quote is **price-locked** for 10 minutes (assumption), and the buyer is never charged above the quoted maximum.

### Bids

- Creators may set bids inside a band declared in the manifest: promotional discounts, time-limited offers, and volume tiers.
- A bid is shown as a tag (for example, "Creator bid: 15% off"), and the comparison table sorts by effective price.
- Prices never change during a running session, and surge pricing is not allowed.

### Comparison view

- Buyers pick up to three agents and see them side by side, with highlights for best value, highest confidence, and fastest.
- A **preview run** (capped at around 20 credits; assumption) lets a buyer test an agent on a small sample before committing the full budget.

### Ratings and reviews

- Only buyers with a settled session can rate, and ratings open 24 hours after the session.
- Creators can reply publicly. Ratings from new or suspicious accounts count less, and review manipulation is investigated.

### Anti-gaming

Creators cannot pay for rank. Listing text is compared against measured behavior. Agents that under-quote, inflate their descriptions, or behave differently under review lose rank and can be delisted.

## 10. Buyer journey and session flow

The buyer goes from describing a task to receiving a result in about ten steps, always seeing price and spend before and during the work.

1. **Sign up and add credits.** Free trial credits, a pack, or a subscription (Section 7).
2. **Describe the task.** Plain text, optionally with files.
3. **See the shortlist.** Ranked agents with price range, confidence, time, and badges.
4. **Compare.** Select up to three for a side-by-side view; optionally run a preview.
5. **Choose and set a budget cap.** The default cap is the quoted p90 price; the buyer can raise or lower it.
6. **Start the session.** The platform holds the cap in escrow and boots the agent.
7. **Work with the agent.** The chat shows messages, questions, a progress bar, a live spend meter against the cap, and a files panel. The buyer can pause for input, answer questions, approve sensitive actions, extend the cap, or cancel.
8. **Receive the result.** Text and files are delivered and stored for the retention period (assumption: 30 days).
9. **Settle and see the receipt.** The final price, unused credits released, and a short usage summary.
10. **Rate or dispute.** A rating after 24 hours; a dispute within 7 days.

### Edge cases

| Situation | What happens |
| --- | --- |
| Balance too low for the cap | The session cannot start; the buyer is offered a top-up or a lower cap |
| Spend reaches 80 percent of the cap | Warning appears with options to extend or let the agent wrap up |
| Cap reached | Hard stop; the agent is told to deliver what it has; refund rules in Section 7 apply |
| Agent asks a question | Status becomes waiting\_for\_user; the time limit pauses and no cost accrues |
| Buyer closes the browser | The session keeps running to the time limit and results wait in the buyer's history |
| Agent crashes or times out | Automatic refund; the buyer can retry with the same agent or pick another |
| Several sessions at once | Allowed; each has its own escrow and cap |
| Buyer wants to switch agents mid-task | The current session is cancelled (charged by the cancellation rule) and files can be passed to a new session |

## 11. Creator journey and agent lifecycle

A creator goes from idea to earning in ten stages, and every stage returns numbers: test scores, measured cost, and live results.

1. **Sign up.** Create a creator profile and accept the creator terms (see policies below).
2. **Scaffold.** `marketplace init` generates a project with the SDK, a manifest, and an example handler.
3. **Build.** Use any framework. Call models through the standard client (the SDK sets the proxy address) and use `ctx` for chat, files, and results.
4. **Run locally.** `marketplace dev` runs the agent against a stub proxy with free test tokens.
5. **Test.** `marketplace test` runs the creator's cases and prints estimated cost and latency.
6. **Submit.** `marketplace submit` (or a GitHub import in the dashboard) sends the version for review with listing text, example tasks, category, and price.
7. **Review.** The creator watches pipeline progress, reads the report card, fixes problems, and resubmits (Section 8).
8. **Publish.** An approved version goes live with a Beta badge and a public listing page.
9. **Operate.** The dashboard shows live performance; the creator answers reviews and ships new versions.
10. **Earn and retire.** Earnings clear and pay out (Section 7). A creator can deprecate an agent with a notice period; running sessions finish.

### Creator dashboard

| Panel | What it shows |
| --- | --- |
| Overview | Sessions, revenue, success rate, confidence trend |
| Cost and margin | Average cost against price, p50 and p90 cost, margin per session, alerts for negative margin |
| Failures | Categories (errors, timeouts, budget stops, refunds) with redacted examples |
| Ratings | Scores and reviews, with replies |
| Versions | Status of each version and rollout state |
| Earnings | Accrued, in clearing, available, and paid out |
| Pricing helper | Suggested price from measured cost and the price band of similar agents |
| Logs | The creator's own debug logs; buyer content stays redacted unless the buyer opts in to share it for debugging |

### Versioning

- Versions follow semantic versioning, and each is reviewed and immutable once approved.
- New versions can roll out to a small share of traffic first, then to everyone (later feature; the MVP switches all at once).
- Rollback to the previous approved version is one click.
- Old versions are retired 30 days after a newer one goes live (assumption).

### Creator policies

- Creators keep ownership of their code and prompts and grant the platform a license to host and run them.
- Agents may not keep buyer content after a session ends, collect data beyond the session, or contact unapproved domains.
- The listing must be honest about what the agent does and its limits.
- Creators must provide a support contact and respond to disputes within a set time (assumption: 3 days).

## 12. Data model

The platform stores money as integer microcredits, keeps usage and ledger records append-only, and ties every session to a buyer, an agent version, and a creator.

| Entity | Key fields | Relationships |
| --- | --- | --- |
| `users` | id, email, roles (buyer, creator, admin), status, created\_at | Has one wallet; may have a creator profile |
| `creator_profiles` | user\_id, display\_name, payout\_account\_ref, verification\_level | Owns agents |
| `agents` | id, creator\_id, slug, category, status (draft, live, paused, delisted), current\_version\_id | Has many agent versions and one listing |
| `agent_versions` | id, agent\_id, semver, image\_ref, manifest\_json, status (submitted, in\_review, approved, rejected, retired), pricing\_model, price\_credits, limits\_json | Has many review runs and sessions |
| `review_runs` | id, agent\_version\_id, started\_at, finished\_at, decision, scores\_json (Q, R, S, K), cost\_p50, cost\_p90, latency\_p50, latency\_p95, report\_url | Has many test results and findings |
| `test_results` | id, review\_run\_id, case\_id, run\_index, passed, score, cost\_microcredits, latency\_ms | Belongs to a review run |
| `review_findings` | id, review\_run\_id, severity, category, detail, status | Belongs to a review run |
| `listings` | agent\_id, title, summary, description, tags, example\_tasks, embedding, badges, confidence\_score, confidence\_n | One per agent |
| `quotes` | id, buyer\_id, agent\_version\_id, low, high, max, expires\_at | Becomes a session |
| `sessions` | id, buyer\_id, agent\_version\_id, quote\_id, status, cap, started\_at, ended\_at, price, cost, fee, creator\_earnings, refund\_reason | Has many messages, files, usage events |
| `session_messages` | id, session\_id, role (buyer, agent, system), type, content\_ref, ts | Belongs to a session |
| `session_files` | id, session\_id, name, storage\_ref, direction (in, out), expires\_at | Belongs to a session |
| `session_tokens` | id, session\_id, token\_hash, scopes, cap, expires\_at, revoked | One per session |
| `usage_events` | event\_id, session\_id, agent\_version\_id, creator\_id, buyer\_id, type, model\_alias, tokens, cost\_microcredits, ts | Append-only; belongs to a session |
| `wallets` | user\_id, cached\_balance | Derived from ledger entries |
| `ledger_accounts` | id, type, owner\_id | Types listed in Section 7 |
| `ledger_entries` | id, txn\_id, account\_id, direction, amount\_microcredits, ref\_type, ref\_id, idempotency\_key, ts | Append-only; every transaction sums to zero |
| `credit_purchases` | id, user\_id, provider\_ref, amount\_usd\_cents, credits, status | Creates ledger entries |
| `subscriptions` | id, user\_id, plan, status, renews\_at | Grants credits on renewal |
| `payouts` | id, creator\_id, amount, status, requested\_at, paid\_at | Draws on creator\_payable |
| `ratings` | id, session\_id, buyer\_id, stars, comment, creator\_reply | One per settled session |
| `disputes` | id, session\_id, reason, status, resolution, refund\_amount | Belongs to a session |
| `model_prices` | alias, provider\_model, price\_in, price\_out, effective\_from | Used by the proxy for cost |
| `tool_prices` | tool, unit, price, effective\_from | Used by the broker for cost |
| `audit_log` | id, actor\_id, action, target, details, ts | Append-only |

### Data rules

- All money fields are integers in microcredits; never floating point.
- `usage_events`, `ledger_entries`, and `audit_log` are append-only. Corrections are new entries.
- Session content (messages and files) is deleted after the retention period (assumption: 30 days); billing records are kept.
- A buyer's wallet balance is always derivable from ledger entries; the cached balance is only for speed.

## 13. API surface

The platform exposes four groups of endpoints: buyer, creator, admin, and runtime; all paths are versioned under `/v1`.

### Buyer API

| Method and path | Purpose |
| --- | --- |
| `POST /v1/search` | Task text and filters in; ranked shortlist with price ranges out |
| `GET /v1/agents/{id}` | Listing, report card summary, ratings, current version |
| `POST /v1/compare` | Up to three agent ids in; side-by-side data out |
| `POST /v1/quotes` | Task and agent in; price range, maximum, and price lock out |
| `POST /v1/sessions` | Quote id and budget cap in; creates the session and holds credits |
| `GET /v1/sessions/{id}` | Status and spend so far |
| `GET /v1/sessions/{id}/events` | Server-sent event stream: messages, questions, progress, files, result, spend updates |
| `POST /v1/sessions/{id}/messages` | Buyer message or answer to an agent question |
| `POST /v1/sessions/{id}/confirm` | Approve or deny a sensitive action the agent asked about |
| `POST /v1/sessions/{id}/extend` | Raise the budget cap |
| `POST /v1/sessions/{id}/cancel` | Stop the session |
| `GET /v1/sessions/{id}/files/{file_id}` | Download an output file |
| `POST /v1/sessions/{id}/rating` | Rate a settled session |
| `POST /v1/sessions/{id}/dispute` | Open a dispute |
| `GET /v1/receipts/{session_id}` | Itemized receipt |
| `GET /v1/wallet` | Balance and history |
| `POST /v1/wallet/topup` | Start a checkout for a credit pack |
| `POST /v1/subscriptions` | Start, change, or cancel a plan |

### Creator API

| Method and path | Purpose |
| --- | --- |
| `POST /v1/creator/agents` | Create an agent record |
| `POST /v1/creator/agents/{id}/versions` | Submit a version (repo URL or image) with manifest |
| `GET /v1/creator/agents/{id}/versions/{v}/review` | Pipeline status and report card |
| `PATCH /v1/creator/agents/{id}/listing` | Edit listing text, examples, and price (applies to new sessions) |
| `POST /v1/creator/agents/{id}/versions/{v}/publish` | Make an approved version live |
| `POST /v1/creator/agents/{id}/rollback` | Return to the previous approved version |
| `GET /v1/creator/analytics` | Sessions, success, cost, margin, ratings |
| `GET /v1/creator/agents/{id}/logs` | The creator's own debug logs |
| `GET /v1/creator/earnings` | Accrued, clearing, and available balances |
| `POST /v1/creator/payouts` | Request a payout |

### Admin API

| Method and path | Purpose |
| --- | --- |
| `GET /v1/admin/review-queue` | Versions waiting for human review |
| `POST /v1/admin/versions/{v}/decision` | Approve, reject, or request changes |
| `POST /v1/admin/agents/{id}/kill` | Kill switch: stop the agent and cancel and refund running sessions |
| `PUT /v1/admin/model-prices` | Update model and tool prices and aliases |
| `PUT /v1/admin/fees` | Change the platform fee rate and markup |
| `GET /v1/admin/disputes` and `POST /v1/admin/disputes/{id}/resolve` | Handle disputes |
| `GET /v1/admin/metrics` | Revenue, cost, margin, volume, failure rates |

### Runtime and internal endpoints

| Endpoint | Used by | Purpose |
| --- | --- | --- |
| `POST {proxy}/v1/chat/completions` (and similar) | Agent containers | OpenAI-compatible model access with the session key |
| `POST {tools}/v1/tools/{name}` | Agent containers | Tool broker calls with the session key |
| `POST /invoke`, `POST /quote`, `GET /health`, `POST /cancel` | Gateway, on the agent | The runtime contract (Section 5) |
| `POST /internal/usage` | Proxy and broker | Write a usage event to metering |
| `POST /internal/settle` | Gateway | Compute the split and post ledger entries at session end |
| `POST /webhooks/payments` | Payment provider | Credit purchases, renewals, chargebacks |
| `POST /webhooks/build` | Build system | Build and scan results |

### Conventions

- Authentication: user token for buyer and creator APIs; scoped session key for runtime calls; service credentials for internal calls.
- Every request that moves money carries an idempotency key.
- Error codes: 402 for insufficient credits, 409 for state conflicts, 429 for rate limits, and `budget_exhausted` for cap hits inside a session.
- Rate limits apply per user, per agent, and per session.

## 14. Security, trust, and abuse prevention

Security rests on five layers: isolate the agent, limit what it can reach, limit what it can spend, verify it before and after launch, and be able to stop it instantly.

### Threats and controls

| Threat | Control |
| --- | --- |
| An agent escapes its sandbox | One container per session, non-root user, read-only filesystem except the workspace, no host mounts, CPU, memory, and process limits, destroyed at session end; stronger isolation (gVisor or microVMs) later |
| An agent steals or misuses provider keys | Real keys never enter containers; agents hold only session virtual keys that expire when the session ends; creator secrets are encrypted and injected only into that creator's agent |
| An agent leaks buyer data | Egress allowlist and logging, no persistent storage, workspace wiped after the session, retention limits, exfiltration probes in review |
| An agent bypasses the meter | Provider domains and raw IPs are blocked; any attempt is logged as a security finding |
| Runaway cost | Spending caps, tool-call limits, timeouts, and loop detection |
| Prompt injection through web pages or files | Tool outputs are marked as untrusted content; sensitive actions require `ctx.confirm` approval from the buyer; review runs injection probes |
| An agent behaves well in review and badly in production | Hidden, randomized tests, canary sessions disguised as real traffic, and continuous monitoring |
| Scam or low-quality agents | Refund rules, confidence scores, delisting, and trust levels for creators |
| Fake reviews and self-dealing | Only settled sessions can rate; fraud signals; earnings withheld on flagged sessions |
| Stolen cards, chargebacks, and promo abuse | Payment provider fraud tools, velocity limits, clawbacks, non-withdrawable promo credits |
| Privacy exposure | Encryption in transit and at rest, minimal data collection, buyer-controlled deletion, access logs; regional privacy law compliance is a later task |
| Insider or admin misuse | Role-based access, a full audit log, and review of sensitive actions such as payouts and fee changes |
| Supply-chain attacks | Dependency scanning, signed and immutable images, and a private registry |

### Creator trust levels

- **New**: lower spending caps for buyers, longer payout clearing window, and extra monitoring.
- **Established**: after a track record of sessions with a good score and few disputes.
- **Trusted**: top performers with shorter clearing windows and higher caps.

Levels are earned from platform data and affect ranking, caps, and payouts.

### Disputes and incidents

1. A buyer opens a dispute within 7 days.
2. The platform assembles the evidence automatically: event timeline, outputs, usage, and review history.
3. A reviewer decides within 3 days (assumption): refund, partial refund, or reject.
4. A refund for agent fault is funded from the creator's earnings or held balance; the creator can appeal.
5. A critical incident triggers the kill switch, cancels and refunds running sessions, and notifies affected buyers.

## 15. Hackathon MVP (12 hours)

In 12 hours we build the thin slice that proves the whole idea: one buyer flow, three or four agents running through the metered proxy, a ledger that settles correctly, and an automated review that produces a report card.

### Real and mocked

| Component | In the MVP | Later or mocked |
| --- | --- | --- |
| Web UI (search, compare, chat, wallet, creator dashboard) | Real, built with Lovable | Polish and more screens |
| Gateway, sessions, live event stream | Real | Scaling |
| Agent runtime | Real: a Docker container (or a subprocess) per session | Stronger isolation |
| SDK | Real, minimal Python: message, ask\_user, progress, write\_file, return\_result | Other languages, more functions |
| LLM proxy with virtual keys and cost | Real, one provider, two model aliases | More providers |
| Tool broker | One real tool (web search) or a mock; local file storage | More tools |
| Credit ledger with hold and settle | Real, double-entry | Subscriptions, expiries |
| Payments | Mock top-up button, or Stripe test mode | Real payments |
| Payouts | Simulated | Real payouts |
| Review pipeline | Real subset: manifest lint, build, 5 to 10 test cases, cost and latency, 3 injection probes, LLM judge, report card | Human review, canaries |
| Search and quotes | Embedding search over about 6 listings; quotes from review data plus seeded sessions | Full estimator |
| Confidence score | Formula implemented with seeded live data | Real live data |
| External endpoint mode | One demo agent | Production hardening |
| Disputes | Kill switch and a refund button only | Full dispute workflow |

### Suggested stack

- Frontend: Lovable (React) plus a charting library.
- Backend: FastAPI (Python) or Node, with SSE for live updates.
- Database: Postgres or SQLite.
- Containers: Docker SDK; asyncio instead of a queue.
- Models: one provider behind the proxy; embeddings from the same provider.
- Storage: local disk. Payments: Stripe test mode, optional.

### Team roles (merge them if the team is small)

1. **Platform lead**: gateway, sessions, runtime, SDK.
2. **Metering and money**: LLM proxy, usage events, ledger, hold and settle.
3. **Review and trust**: pipeline, report card, confidence score.
4. **Frontend and demo**: Lovable UI, seeded data, pitch.

### Demo agents to build

| Agent | Style | Purpose in the demo |
| --- | --- | --- |
| Invoice Auditor | Plain Python, multi-step, per-task price | Shows a basic metered agent |
| Trip Planner | Built with an agent framework, asks the buyer questions, per-task price | Shows "any framework" and chat interaction |
| Research Briefer | External endpoint (Mode B), per-message price | Shows the open door and partial metering |
| Scam Agent | Intentionally bad: plausible nonsense, tries a blocked domain | Fails review live and proves the trust story |

### Hour-by-hour plan

1. **Hour 0 to 1:** Lock this spec, set up the repo and database schema, fix the model aliases, choose the demo agents.
2. **Hours 1 to 3:** Runtime, SDK, and gateway sessions; run Invoice Auditor end to end in a container. The frontend starts in parallel against mocked APIs.
3. **Hours 3 to 5:** LLM proxy, usage events, the cost formula, and the live spend meter.
4. **Hours 5 to 6:** Ledger, hold and settle, wallet, and receipts.
5. **Hours 6 to 8:** Review pipeline and report card; run it on all four agents and seed the results.
6. **Hours 8 to 9:** Search, compare, quotes, and the confidence score in the UI.
7. **Hours 9 to 10:** Creator dashboard (analytics, earnings, submit flow), kill switch, and refund.
8. **Hours 10 to 11:** Rehearse the demo, record a backup video, and pre-warm caches.
9. **Hours 11 to 12:** Fix bugs, polish, finish the pitch, and keep a buffer.

### Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| Model latency or outage | Cache responses, use a seeded replay, use the faster alias |
| Slow container start | Pre-warm containers or use a subprocess |
| Bad venue network | Backup video and a locally recorded run |
| Non-deterministic review results | Fixed seeds; pre-run and store the results |
| Cost overrun | Low caps and cheap models |
| Scope creep | Cut in this order: external mode, search re-ranking, subscriptions, canaries, polish of secondary screens |

## 16. Demo script, open decisions, and glossary

The pitch in one line: **the app store for AI agents, where every agent is tested before you pay and every cent is metered.**

### Demo script (about 4 minutes)

1. **The problem (20 seconds).** Thousands of agents exist, and buyers cannot tell which one works, what it costs, or whether it is safe.
2. **The creator side (30 seconds).** Submit the Trip Planner repo and watch the review pipeline produce a report card (pre-run or sped up).
3. **The buyer side (40 seconds).** Describe a task, see the shortlist, and compare price range, confidence with sample size, and speed.
4. **A live session (60 seconds).** Chat with the agent, answer one of its questions, and watch the spend meter tick and the usage events show tokens passing through our proxy.
5. **Settlement (30 seconds).** Show the receipt and the split into creator earnings, platform fee, and model cost; then the creator dashboard updating.
6. **Trust (40 seconds).** Submit the Scam Agent and watch it fail review; then hit the kill switch on a misbehaving agent and show running sessions cancelled and refunded.
7. **Close (20 seconds).** Agents will transact with people and each other at scale; this is the trust and billing layer they need.

### Open decisions and assumptions

| Decision | Assumed default | Alternatives |
| --- | --- | --- |
| Platform name | To be decided | Any |
| Credit value | 1 credit = $0.01 | Other pegs |
| Platform fee | 20 percent of the agent price | Tiered by creator trust level |
| Who bears model cost | Creator, through all-in pricing | Pass-through billing to the buyer |
| Where agents run | Platform sandbox (primary); external endpoints as Mode B | Creator-hosted only |
| Cancellation charge | Per-task: 50 percent once work has started | Pro-rata by progress |
| Credit expiry | Purchased credits last 12 months; promo credits expire sooner | No expiry |
| Payout clearing window | 14 days | 7 or 30 days |
| Minimum payout | $25 | Other amounts |
| Session data retention | 30 days | Shorter, or buyer-chosen |
| Quote price lock | 10 minutes | Other durations |
| Preview run cap | About 20 credits | Free previews for a limited number per user |
| Platform cost markup (m) | 0 to 10 percent | Fixed fee per session |
| Refund window for unused credits | 14 days | None |

### Glossary

| Term | Meaning |
| --- | --- |
| Agent | A full program with its own workflow that completes a task for a buyer |
| Agent version | One immutable, reviewed build of an agent |
| Buyer | A user who pays credits to run agents |
| Creator | A user who builds and publishes agents and earns from them |
| Session | One run of an agent for a buyer, with its own budget cap, escrow, and logs |
| Gateway | The platform component that authenticates, routes, and limits all traffic between buyers and agents |
| Sandbox | The isolated container where an agent runs |
| Manifest | The `agent.yaml` file describing an agent, its limits, tools, and pricing |
| SDK | The Marketplace library that connects an agent to the platform |
| Runtime contract | The routes and events every agent implements: invoke, quote, health, cancel |
| LLM proxy | The OpenAI-compatible service that all agent model calls pass through |
| Virtual key | A session-scoped, expiring key an agent uses with the proxy |
| Tool broker | The service that provides and meters platform tools |
| Egress proxy | The gate that allows only approved outbound network destinations |
| Usage event | One append-only record of a metered action |
| Credit and microcredit | The internal currency, and its integer unit (1 credit = 1,000 microcredits) |
| Hold and escrow | Credits reserved from the buyer's wallet for a session |
| Settlement | Computing the final price and split, and releasing unused credits |
| Ledger | The double-entry record of every credit movement |
| Quote | The platform's estimated price range and maximum for a task and agent |
| Bid | A creator's discount or offer inside their allowed price band |
| Report card | The review result for an agent version |
| Confidence score | The platform-computed measure of how reliably an agent does its job, shown with its sample size |
| Canary | A hidden test task mixed into real traffic to catch agents that behave differently under test |
| Kill switch | The admin control that stops an agent version immediately |
| Model alias | A platform name such as fast or strong that maps to a real provider model |
| Mode A, B, C | Hosted container, external endpoint, and no-code template integration |
| MVP | The scope built in the 12-hour hackathon |
