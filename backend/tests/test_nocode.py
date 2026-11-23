"""No-code agents: the marketplace_sdk.nocode runner, startup discovery, review and a buyer session."""
import shutil
import sys
import time
from pathlib import Path

import httpx
import yaml

from app.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))
from marketplace_sdk import BudgetExceeded  # noqa: E402
from marketplace_sdk import nocode as runner  # noqa: E402

TEMPLATE = REPO_ROOT / "agents" / "_template"
SAM = {"authorization": "Bearer tok_sam"}
ADMIN = {"authorization": "Bearer tok_admin"}


class FakeCtx:
    """Records what the runner does; llm replies and buyer answers are scripted."""
    def __init__(self, task, replies, answers, files=None):
        self.task = type("T", (), {"text": task, "files": list(files or {})})()
        self._files, self.replies, self.answers = dict(files or {}), list(replies), list(answers)
        self.calls, self.asked, self.written, self.result = [], [], {}, None

    def progress(self, *a, **k): pass
    def read_file(self, name): return self._files[name]
    def llm(self, messages, model="fast", **params):
        self.calls.append((model, [dict(m) for m in messages], params))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    def ask_user(self, q, options=None):
        self.asked.append(("ask", q, options))
        return self.answers.pop(0)
    def confirm(self, action):
        self.asked.append(("confirm", action, None))
        return self.answers.pop(0)
    def write_file(self, name, data, mime=None): self.written[name] = data
    def return_result(self, text, files=None): self.result = (text, files or [])


def manifest(**nocode):
    m = yaml.safe_load((TEMPLATE / "agent.yaml").read_text())
    m["nocode"] = {**m["nocode"], **nocode}
    return m


def test_directive_and_render():
    assert runner.directive("ASK: Which city? | Lisbon | Porto\n") == ("ask", "Which city?", ["Lisbon", "Porto"])
    assert runner.directive("\n  CONFIRM: Book the hotel") == ("confirm", "Book the hotel", [])
    assert runner.directive("## Plan\nASK: not a directive")[0] == "answer"
    assert runner.render("{{task}} for {{ answers.who }} {{files}} {{other}}", "T", {"who": "me"}, "F") == "T for me F {{other}}"
    s = runner.settings({"models_allowed": ["strong", "balanced"], "nocode": {"model": "strong", "max_turns": 99}})
    assert s["model"] == "balanced" and s["max_turns"] == 6


def test_runner_questions_model_ask_confirm_and_file():
    ctx = FakeCtx("Ana sends budget Friday.", ["ASK: Which team? | Ops | Sales", "CONFIRM: Email the summary",
                                               "## Decisions\n- Ana sends budget"],
                  ["My team", "Ops", True, True], files={"notes.txt": b"extra notes"})
    runner.run_task(ctx, manifest(), TEMPLATE)
    assert [a[0] for a in ctx.asked] == ["ask", "ask", "confirm", "confirm"]
    assert ctx.asked[0] == ("ask", "Who will read these notes?", ["Just me", "My team", "Leadership"])
    assert ctx.asked[1] == ("ask", "Which team?", ["Ops", "Sales"])
    system = ctx.calls[0][1][0]["content"]
    assert "for My team" in system and "Ana sends budget Friday." in system and "extra notes" in system
    assert "ASK:" in system and ctx.calls[0][0] == "fast" and ctx.calls[0][2] == {"max_tokens": 1200}
    assert ctx.calls[2][1][-1]["content"].startswith("The buyer approved")
    assert ctx.result == ("## Decisions\n- Ana sends budget", ["meeting-notes.md"])
    assert ctx.written["meeting-notes.md"].startswith("## Decisions")


def test_runner_declined_save_budget_and_turn_limit():
    ctx = FakeCtx("notes", ["Summary"], ["Just me", False])
    runner.run_task(ctx, manifest(), TEMPLATE)
    assert ctx.result == ("Summary", []) and not ctx.written  # declined: answer shown, no file
    ctx = FakeCtx("notes", [BudgetExceeded("cap")], ["Just me", True])
    runner.run_task(ctx, manifest(), TEMPLATE)
    assert ctx.result[0].startswith("The budget ran out")
    ctx = FakeCtx("notes", ["ASK: more?"], ["Just me", True])
    runner.run_task(ctx, manifest(max_turns=1), TEMPLATE)
    assert "couldn't finish" in ctx.result[0] and len(ctx.calls) == 1


def test_discover_review_publish_and_session(live_server, tmp_path):
    from app.db import SessionLocal
    from app.services import nocode
    folder = tmp_path / "standup-notes"
    shutil.copytree(TEMPLATE, folder)
    m = yaml.safe_load((folder / "agent.yaml").read_text()) | {"name": "standup-notes", "display_name": "Standup Notes"}
    (folder / "agent.yaml").write_text(yaml.safe_dump(m))
    shutil.copytree(TEMPLATE, tmp_path / "_skipped")
    db = SessionLocal()
    [vid] = nocode.discover(db, tmp_path)
    assert nocode.discover(db, tmp_path) == []  # idempotent by slug + version
    db.close()
    with httpx.Client(base_url=live_server, timeout=180) as c:
        r = c.post(f"/v1/admin/versions/{vid}/review", headers=ADMIN).json()
        assert r["decision"] == "approved", r
        stages = [s["stage"] for s in r["report_card"]["stages"]]
        assert len(stages) == 9 and stages[1] == "build"
        card = next(a for a in c.get("/v1/agents").json() if a["slug"] == "standup-notes")
        sid = c.post("/v1/sessions", headers=SAM, json={"agent_id": card["id"], "task": "Ana sends the budget Friday.",
                                                        "cap_credits": 15}).json()["session_id"]
        seen = []
        for _ in range(240):
            s = c.get(f"/v1/sessions/{sid}", headers=SAM).json()
            q = s["pending_question"]
            if q and q["id"] not in [x[1] for x in seen]:
                seen.append((q["type"], q["id"]))
                if q["type"] == "confirm":
                    c.post(f"/v1/sessions/{sid}/confirm", headers=SAM, json={"confirm_id": q["id"], "approved": True})
                else:
                    c.post(f"/v1/sessions/{sid}/messages", headers=SAM, json={"text": q["options"][1], "reply_to": q["id"]})
            if s["settled_at"]:
                break
            time.sleep(0.25)
        assert [t for t, _ in seen] == ["ask_user", "confirm"] and s["status"] == "settled"
        assert "Ana" in s["result_text"] and [f["name"] for f in s["files"]] == ["meeting-notes.md"]
        assert c.get(f"/v1/receipts/{sid}", headers=SAM).json()["price_credits"] == 15
