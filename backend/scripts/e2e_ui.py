"""End-to-end UI run of frontend/index.html against `make dev` (fake mode): buyer, creator, admin, error paths.

    make seed && make dev                      # terminal 1 (fresh seed; wait ~25 s for the InvoiceGenius Pro review)
    pip install playwright && playwright install chromium
    python backend/scripts/e2e_ui.py           # terminal 2; writes backend/data/e2e.json and e2e_last.png
"""
import json, sys, time, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = str(Path(__file__).resolve().parents[1] / "data")
REPO = Path(__file__).resolve().parents[2]
ROWS = []


def step(role, name, where):
    def deco(fn):
        t0 = time.time()
        try:
            note = fn() or ""
            ROWS.append((role, name, "PASS", where, str(note)))
        except Exception as e:  # noqa: BLE001
            ROWS.append((role, name, "FAIL", where, f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"))
            traceback.print_exc()
        print(f"[{ROWS[-1][2]}] {role} · {name} ({time.time()-t0:.1f}s) {ROWS[-1][4][:150]}", flush=True)
        return fn
    return deco


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


with sync_playwright() as p:
    try:
        b = p.chromium.launch(headless=True)
    except Exception:  # no bundled Chromium: use the installed Chrome
        b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    errors, calls = [], []
    page.on("console", lambda m: m.type == "error" and "favicon" not in m.text and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: (errors.append("dialog: " + d.message), d.dismiss()))
    page.on("request", lambda r: "/v1/" in r.url and calls.append((r.method, r.url.replace(BASE, "").split("?")[0], r.headers.get("idempotency-key"), r.headers.get("authorization"))))
    api = lambda method, path, token, body=None, headers=None: ctx.request.fetch(
        BASE + path, method=method, headers={"Authorization": "Bearer " + token, **(headers or {})},
        data=json.dumps(body) if body is not None else None)

    def balance(token="tok_sam"):
        return api("GET", "/v1/wallet", token).json()["balance_credits"]

    def login(token):
        page.goto(BASE + "/")
        page.evaluate("localStorage.removeItem('cohort-demo-token')")
        page.goto(BASE + "/")
        page.wait_for_selector("#auth:not(.hidden)", timeout=10000)
        label = {"tok_sam": "Sam", "tok_riya": "Riya", "tok_admin": "Admin"}[token]
        page.locator("#auth-options button.auth-quiet", has_text=label).click()
        page.wait_for_selector("#auth.hidden", state="attached", timeout=5000)
        page.wait_for_function("state.me && state.me.roles && state.me.roles.length && document.querySelector('#credit-total').textContent !== '0'", timeout=5000)

    def nav(view):
        if view == "creator":  # the account menu holds Creator in this design
            page.locator("#user").click()
            page.locator("#user-menu [data-view=creator]").click()
        else:
            page.locator(f"aside .nav-item[data-view={view}]").click()

    def run_session(answer_text="About 2000 USD"):
        seen = {"ask": 0, "confirm": 0}
        deadline = time.time() + 90
        while time.time() < deadline and not page.locator("#receipt").inner_text().strip():
            ask = page.locator("#ask")
            if ask.is_visible():
                if ask.get_by_text("Approve").count():
                    seen["confirm"] += 1
                    ask.get_by_text("Approve").click()
                elif ask.locator("input").count():
                    seen["ask"] += 1
                    ask.locator("input").fill(answer_text)
                    ask.get_by_text("Send").click()
                else:
                    seen["ask"] += 1
                    ask.locator("button").first.click()
                time.sleep(0.4)
            time.sleep(0.2)
        return seen

    state = {}

    # ---------------- Buyer ----------------
    @step("Buyer", "Demo login (3 buttons, Sam)", "auth overlay · GET /v1/auth/config, /v1/me, /v1/wallet")
    def _():
        page.goto(BASE + "/")
        page.wait_for_selector("#auth:not(.hidden)", timeout=10000)
        btns = page.locator("#auth-options button.auth-quiet").all_inner_texts()
        check(len(btns) == 3, btns)
        check(page.locator("#auth-google").count() == 1, "Google (Supabase) button missing")
        login("tok_sam")
        return f"{btns}; balance {page.locator('#credit-left').inner_text()}"

    @step("Buyer", "Library from GET /v1/agents", "Library page · GET /v1/agents")
    def _():
        nav("library")
        page.wait_for_selector("#library-grid .agent-card", timeout=5000)
        titles = [t.split("\n")[0] for t in page.locator("#library-grid .agent-card h3").all_inner_texts()]
        check(set(titles) == {"Invoice Auditor", "Trip Planner", "Research Briefer", "Quick Summarizer"}, titles)
        check(page.locator("#library-grid button.choose", has_text="Use").count() == 4, "Use buttons")
        return titles

    @step("Buyer", "Search", "chat · POST /v1/conversations, /stream")
    def _():
        page.locator("#new-chat").click()
        page.fill("#task", "Plan a 5-day trip to Tokyo under $2,000")
        page.press("#task", "Enter")
        page.wait_for_selector(".match", timeout=15000)
        cards = [t.split("\n")[1] for t in page.locator(".match").all_inner_texts()]
        check(any("Trip Planner" in c for c in cards), cards)
        stream = [c for c in calls if c[1].endswith("/stream")]
        check(stream and stream[-1][3], "stream call without Authorization")
        return cards

    @step("Buyer", "Compare 2 agents", "Compare view · POST /v1/compare")
    def _():
        page.locator("#new-chat").click()
        page.fill("#task", "Summarize and brief me on research about invoices")
        page.press("#task", "Enter")
        page.wait_for_selector(".match", timeout=15000)
        boxes = page.locator(".match input[data-pick]")
        check(boxes.count() >= 2, f"{boxes.count()} compare boxes")
        boxes.nth(0).check(); boxes.nth(1).check()
        page.locator("#run-compare").click()
        page.wait_for_selector("#compare:not(.hidden) .cmp", timeout=5000)
        n = page.locator("#compare .cmp").count()
        check(n == 2, n)
        return page.locator("#compare .cmp .flag").all_inner_texts()

    @step("Buyer", "Report card", "Report view · GET /v1/agents/{id}")
    def _():
        page.locator("#compare .cmp button[data-detail]").first.click()
        page.wait_for_selector("#report:not(.hidden) .card-block", timeout=5000)
        txt = page.locator("#report-body").inner_text()
        check("Decision" in txt and "Confidence" in txt, txt[:200])
        page.locator("#report-back").click()
        return txt.split("\n")[0] + " · " + " ".join(l for l in txt.split("\n") if l.startswith("Decision"))

    @step("Buyer", "Quote (cap defaults to high_credits)", "quote sheet · POST /v1/quotes")
    def _():
        page.locator("#new-chat").click()
        page.fill("#task", "Plan a 5-day trip to Tokyo under $2,000")
        page.press("#task", "Enter")
        page.wait_for_selector(".match", timeout=15000)
        page.locator(".match", has_text="Trip Planner").locator("button.choose").click()
        page.wait_for_selector("#quote:not(.hidden)", timeout=5000)
        cap = page.input_value("#quote-cap")
        q = api("POST", "/v1/quotes", "tok_sam", {"agent_id": "agt_trip_planner", "task": "x"}, {"Content-Type": "application/json"}).json()
        check(float(cap) == q["high_credits"], (cap, q["high_credits"]))
        page.set_input_files("#quote-file", files=[{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"window seat, vegetarian"}])
        return f"cap {cap} = high_credits; {page.locator('#quote-price').inner_text()}"

    @step("Buyer", "Start session (Idempotency-Key, double-click)", "quote Start · POST /v1/sessions")
    def _():
        before = len(api("GET", "/v1/sessions", "tok_sam").json()["sessions"])
        page.locator("#quote-start").dblclick()
        page.wait_for_selector("#session-live:not(.hidden)", timeout=5000)
        time.sleep(0.5)
        posts = [c for c in calls if c[0] == "POST" and c[1] == "/v1/sessions"]
        check(all(c[2] for c in posts), "POST /v1/sessions without Idempotency-Key")
        after = len(api("GET", "/v1/sessions", "tok_sam").json()["sessions"])
        check(after - before == 1, f"{after-before} sessions created")
        return f"{len(posts)} POST(s), {after-before} session created"

    @step("Buyer", "Answer a question + approve a confirm", "live session · SSE, POST /messages, /confirm")
    def _():
        seen = run_session()
        check(seen["ask"] >= 1 and seen["confirm"] >= 1, seen)
        bub = page.locator("#log .bubble").all_inner_texts()
        check(not any(t in ("spend", "status", "budget_warning") for t in bub), "spend/status shown as bubbles")
        state["sid"] = page.evaluate("state.session")
        return f"{seen}; bubbles {len(bub)}, none for spend/status"

    @step("Buyer", "Result, file link, receipt", "live session · GET /files/{id}, /v1/receipts/{id}")
    def _():
        links = page.locator("#log a[download]")
        check(links.count() >= 1, "no file link")
        with page.expect_download() as dl:
            links.first.click()
        size = len(open(dl.value.path(), "rb").read())
        check(size > 0, "empty download")
        rec = page.locator("#receipt").inner_text()
        check("Charged 120 credits" in rec, rec)
        return f"{links.first.inner_text()} ({size} B); " + rec.replace("\n", " ")

    @step("Buyer", "Rate the agent", "after receipt · POST /rating")
    def _():
        page.fill("#rate-stars", "5"); page.fill("#rate-comment", "Great plan")
        page.locator("#rate-form button[type=submit]").click()
        page.wait_for_function("document.querySelector('#after-note').textContent.length > 0", timeout=5000)
        note = page.locator("#after-note").inner_text()
        check(note == "Rating saved.", note)
        return note

    @step("Buyer", "Wallet page (balance, history, pack)", "Wallet · GET /v1/wallet, POST /v1/wallet/topup {pack}")
    def _():
        nav("wallet")
        page.wait_for_selector("#wallet-body .kv", timeout=5000)
        hist = page.locator("#wallet-body .kv").count()
        b0 = balance()
        page.locator("#wallet-body [data-pack=S]").dblclick()
        time.sleep(1.5)
        d = balance() - b0
        check(d == 1000, f"pack S added {d}")
        return f"{hist} rows; pack S double-click added {d:g} credits once"

    @step("Buyer", "Top-up double-click charges once", "account menu Upgrade · POST /v1/wallet/topup {credits}")
    def _():
        b0 = balance()
        page.locator("#user").click()
        page.locator("#add-credits").dblclick()
        time.sleep(1.5)
        d = balance() - b0
        keys = [c[2] for c in calls if c[1] == "/v1/wallet/topup"]
        check(all(keys), "top-up without Idempotency-Key")
        # same key twice straight at the API must also charge once
        b1 = balance()
        for _ in range(2):
            api("POST", "/v1/wallet/topup", "tok_sam", {"credits": 500}, {"Content-Type": "application/json", "Idempotency-Key": "e2e-dup"})
        d2 = balance() - b1
        check(d == 500 and d2 == 500, (d, d2))
        return f"UI double-click +{d:g}; same key twice at API +{d2:g}"

    # ---------------- Creator ----------------
    @step("Creator", "Dashboard", "Creator · GET /v1/me, /v1/creator/analytics, /earnings")
    def _():
        login("tok_riya")
        nav("creator")
        page.wait_for_selector("#creator-home .kv", timeout=5000)
        txt = page.locator("#creator-home").inner_text().replace("\n", " ")
        check("Sessions" in txt and "Earnings" in txt, txt)
        return txt[:160]

    @step("Creator", "Submit an agent (5 steps)", "Creator form · POST /v1/creator/agents, /versions")
    def _():
        state["slug"] = f"echo-e2e-{int(time.time())}"
        page.fill("#c-slug", state["slug"]); page.fill("#c-category", "writing")
        page.click("#c-next"); page.fill("#c-version", "1.0.0"); page.fill("#c-price", "8")
        page.click("#c-next"); page.fill("#c-title", "Echo E2E"); page.fill("#c-summary", "Drafts and tightens an answer.")
        page.click("#c-next"); page.fill("#c-samples", "Summarize the key points of remote work")
        page.click("#c-next"); page.fill("#c-source", "sdk/python/examples/echo_agent")
        page.click("#c-submit")
        page.wait_for_function("document.querySelector('#c-submit-note').textContent.length > 0", timeout=8000)
        note = page.locator("#c-submit-note").inner_text()
        check("in_review" in note, note)
        return note

    @step("Creator", "Live 9-stage review", "Creator review panel · GET /versions/{v}/review (polled)")
    def _():
        seen, deadline = [], time.time() + 120
        while time.time() < deadline:
            txt = page.locator("#creator-review").inner_text()
            stage = next((l for l in txt.split("\n") if l.startswith("Current stage")), "")
            if stage and (not seen or seen[-1] != stage):
                seen.append(stage)
            if "· done" in stage:
                break
            time.sleep(0.3)
        check(seen and "· done" in seen[-1], seen)
        stages = [l for l in page.locator("#creator-review li").all_inner_texts() if " · " in l]
        check(len(stages) == 9, stages)
        return f"{len(seen)} live updates; stages: " + ", ".join(s.split(" · ")[0] for s in stages)

    @step("Creator", "Report card", "Creator review panel")
    def _():
        txt = page.locator("#creator-review .card-block").inner_text()
        check("Decision" in txt, txt)
        return " · ".join(l for l in txt.split("\n") if l.startswith(("Decision", "Tests", "Cost")))

    # ---------------- Admin ----------------
    @step("Admin", "Review queue + metrics", "Review · GET /v1/admin/metrics, /review-queue, /agents")
    def _():
        login("tok_admin")
        nav("admin")
        page.wait_for_selector("#admin-body .cmp", timeout=5000)
        m = page.locator("#admin-metrics").inner_text().replace("\n", " ")
        check("Settled sessions" in m, m)
        return "metrics: " + m + " | queue: " + page.locator("#admin-queue").inner_text().replace("\n", " ")[:80]

    @step("Admin", "InvoiceGenius Pro rejected with findings", "Review · agent card")
    def _():
        card = page.locator("#admin-body .cmp", has_text="InvoiceGenius Pro").inner_text()
        check("rejected" in card and "undeclared" in card, card)
        return " ".join(card.split("\n")[1:4])[:180]

    @step("Admin", "Kill switch refunds a running session", "Review · POST /v1/admin/agents/{id}/kill")
    def _():
        sam0 = balance()
        s = api("POST", "/v1/sessions", "tok_sam", {"agent_id": "agt_trip_planner", "task": "Plan Lisbon", "cap_credits": 120},
                {"Content-Type": "application/json"}).json()
        state["killed"] = s["session_id"]
        for _ in range(30):
            if api("GET", f"/v1/sessions/{s['session_id']}", "tok_sam").json()["status"] == "awaiting_user":
                break
            time.sleep(0.5)
        held = sam0 - balance()
        page.locator("#admin-body .cmp", has_text="Trip Planner").locator("[data-kill]").click()
        page.wait_for_function("document.querySelector('#admin-error').textContent.startsWith('Paused')", timeout=10000)
        msg = page.locator("#admin-error").inner_text()
        time.sleep(1)
        st = api("GET", f"/v1/sessions/{s['session_id']}", "tok_sam").json()["status"]
        back = balance() - (sam0 - held)
        check("Refunded 1" in msg and st == "cancelled" and abs(back - held) < 1e-6, (msg, st, held, back))
        return f"{msg} session {st}; held {held:g} returned {back:g}"

    @step("Errors", "Paused agent", "Sam: quote Start · POST /v1/sessions -> 409/agent_unavailable")
    def _():
        login("tok_sam")
        page.evaluate("state.task = 'Plan Lisbon'")
        page.evaluate("openQuote('agt_trip_planner')")
        page.wait_for_selector("#quote:not(.hidden)", timeout=5000)
        e = page.locator("#quote-error").inner_text()
        check("paused" in e.lower() and page.locator("#quote-start").is_disabled(), e)
        page.locator("#quote-back").click()
        return e

    @step("Errors", "Insufficient credits", "Admin (0 credits): quote sheet + POST /v1/sessions -> 402")
    def _():
        login("tok_admin")
        bal = balance("tok_admin")
        page.evaluate("state.task = 'Audit this invoice'")
        page.evaluate("openQuote('agt_invoice_auditor')")
        page.wait_for_selector("#quote:not(.hidden)", timeout=5000)
        ui = page.locator("#quote-error").inner_text()
        check(page.locator("#quote-start").is_disabled() and page.locator("#quote-topup").is_visible(), "start not blocked")
        page.evaluate("start('agt_invoice_auditor', 60)")
        page.wait_for_function("document.querySelector('#quote-error').textContent.includes('Add credits')", timeout=5000)
        srv = page.locator("#quote-error").inner_text()
        page.locator("#quote-back").click()
        return f"balance {bal:g}; UI: '{ui}' · server: '{srv}'"

    @step("Errors", "Budget cap exceeded", "live session · budget_warning/budget_exhausted events")
    def _():
        login("tok_sam")
        page.evaluate("state.task = 'Summarize the key points of remote work'")
        page.evaluate("openQuote('agt_quick_summarizer')")
        page.wait_for_selector("#quote:not(.hidden)", timeout=5000)
        page.fill("#quote-cap", "0.01")
        page.evaluate("start('agt_quick_summarizer', 0.01)")
        page.wait_for_selector("#session-live:not(.hidden)", timeout=5000)
        run_session("Keep it short")
        edge = page.locator("#session-edge").inner_text()
        rec = page.locator("#receipt").inner_text().replace("\n", " ")
        check("cap is used up" in edge, edge)
        return f"'{edge}' · {rec}"

    @step("Admin", "Unpause", "Review · POST /v1/admin/agents/{id}/unpause")
    def _():
        login("tok_admin")
        nav("admin")
        page.wait_for_selector("#admin-body .cmp", timeout=5000)
        page.locator("#admin-body .cmp", has_text="Trip Planner").locator("[data-unpause]").click()
        time.sleep(1)
        card = page.locator("#admin-body .cmp", has_text="Trip Planner").inner_text()
        status = next(l for l in card.split("\n") if " · " in l)
        check(status.startswith("live"), card)
        return "Trip Planner " + status

    @step("Admin", "Refund a settled session", "Review · POST /v1/admin/sessions/{id}/refund")
    def _():
        sam0 = balance()
        page.fill("#refund-id", state["sid"])
        page.locator("#refund-form button[type=submit]").click()
        page.wait_for_function("document.querySelector('#refund-note').textContent.length > 0", timeout=5000)
        note = page.locator("#refund-note").inner_text()
        d = balance() - sam0
        check(note.startswith("Refunded") and d > 0, (note, d))
        return f"{note} Sam +{d:g}"

    page.screenshot(path=f"{OUT}/e2e_last.png", full_page=True)
    print("console errors:", errors or "none")
    json.dump({"rows": ROWS, "errors": errors}, open(f"{OUT}/e2e.json", "w"), indent=1)
    b.close()
