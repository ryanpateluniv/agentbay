"""Creator publish API for no-code agents: JSON and zip upload, submit, review status, admin approve/reject."""
import io
import time
import zipfile

import httpx
import yaml

from app.config import REPO_ROOT

TEMPLATE = REPO_ROOT / "agents" / "_template"
ADMIN = {"authorization": "Bearer tok_admin"}


def creator_headers(c, email):
    tok = c.post("/v1/auth/signup", json={"email": email, "password": "long enough 1"}).json()["token"]
    h = {"authorization": "Bearer " + tok}
    c.post("/v1/me/creator", headers=h)
    return h


def template(name, version="1.0.0", **changes):
    m = yaml.safe_load((TEMPLATE / "agent.yaml").read_text()) | {"name": name, "version": version} | changes
    return {"manifest": m, "prompt": (TEMPLATE / "prompt.md").read_text(), "cases": (TEMPLATE / "cases.jsonl").read_text()}


def zipped(body, prefix="my-agent/", extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(prefix + "agent.yaml", yaml.safe_dump(body["manifest"]))
        z.writestr(prefix + "prompt.md", body["prompt"])
        z.writestr(prefix + "cases.jsonl", body["cases"])
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def wait_review(c, h, agent_id, vid):
    for _ in range(400):
        r = c.get(f"/v1/creator/agents/{agent_id}/versions/{vid}/review", headers=h).json()
        if r["review"] and r["review"]["status"] == "done":
            return r
        time.sleep(0.25)
    raise AssertionError("review did not finish")


def listed_slugs(c):
    return {a["slug"] for a in c.get("/v1/agents").json()}


def test_publish_review_and_admin_approve_then_reject_new_version(live_server):
    with httpx.Client(base_url=live_server, timeout=60) as c:
        h = creator_headers(c, "pub@example.com")
        r = c.post("/v1/creator/template-agents", headers=h, json=template("team-notes"))
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["status"] == "draft" and d["slug"] == "team-notes"
        st = c.get(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/review", headers=h).json()
        assert st["review"] is None and st["listed"] is False and st["version_status"] == "draft"

        sub = c.post(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/submit", headers=h)
        assert sub.status_code == 202
        assert c.post(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/submit", headers=h).status_code == 409
        st = wait_review(c, h, d["agent_id"], d["version_id"])
        assert st["review"]["decision"] == "approved" and st["awaiting_admin"] and not st["listed"]
        assert st["version_status"] == "in_review" and "team-notes" not in listed_slugs(c)
        assert d["version_id"] in [q["version_id"] for q in c.get("/v1/admin/review-queue", headers=ADMIN).json()]

        ok = c.post(f"/v1/admin/versions/{d['version_id']}/decision", headers=ADMIN, json={"decision": "approved"})
        assert ok.json()["status"] == "approved"
        st = c.get(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/review", headers=h).json()
        assert st["listed"] and st["agent_status"] == "live" and not st["awaiting_admin"]
        assert "team-notes" in listed_slugs(c)

        # a new version by zip upload, rejected by the admin: stays hidden, the live version keeps serving
        up = c.post("/v1/creator/template-agents/upload", headers=h | {"content-type": "application/zip"},
                    content=zipped(template("team-notes", "1.0.1")))
        assert up.status_code == 201, up.text
        v2 = up.json()["version_id"]
        c.post(f"/v1/creator/agents/{d['agent_id']}/versions/{v2}/submit", headers=h)
        wait_review(c, h, d["agent_id"], v2)
        c.post(f"/v1/admin/versions/{v2}/decision", headers=ADMIN, json={"decision": "rejected", "notes": "Prompt too vague"})
        st = c.get(f"/v1/creator/agents/{d['agent_id']}/versions/{v2}/review", headers=h).json()
        assert st["version_status"] == "rejected" and not st["listed"]
        assert any(f["category"] == "admin_review" and f["detail"] == "Prompt too vague" for f in st["review"]["findings"])
        card = next(a for a in c.get("/v1/agents").json() if a["slug"] == "team-notes")
        assert c.get(f"/v1/agents/{card['id']}").json()["current_version"]["semver"] == "1.0.0"


def test_rejected_new_agent_stays_hidden(live_server):
    with httpx.Client(base_url=live_server, timeout=60) as c:
        h = creator_headers(c, "hidden@example.com")
        d = c.post("/v1/creator/template-agents", headers=h, json=template("hidden-notes")).json()
        c.post(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/submit", headers=h)
        wait_review(c, h, d["agent_id"], d["version_id"])
        c.post(f"/v1/admin/versions/{d['version_id']}/decision", headers=ADMIN, json={"decision": "rejected"})
        assert "hidden-notes" not in listed_slugs(c)
        mine = {a["slug"]: a for a in c.get("/v1/creator/agents", headers=h).json()}
        assert mine["hidden-notes"]["status"] == "draft" and mine["hidden-notes"]["versions"][0]["status"] == "rejected"


def test_validation_ownership_and_zip_safety(live_server):
    with httpx.Client(base_url=live_server, timeout=60) as c:
        h = creator_headers(c, "val@example.com")
        other = creator_headers(c, "other@example.com")
        assert c.post("/v1/creator/template-agents", headers=h, json=template("val-notes")).status_code == 201
        again = c.post("/v1/creator/template-agents", headers=h, json=template("val-notes"))
        assert again.status_code == 422 and "bump" in again.json()["detail"]
        taken = c.post("/v1/creator/template-agents", headers=other, json=template("val-notes", "2.0.0"))
        assert taken.status_code == 422 and "taken" in taken.json()["detail"]
        strong = c.post("/v1/creator/template-agents", headers=h, json=template("val-2", models_allowed=["strong"]))
        assert strong.status_code == 422 and any("fast and balanced" in p for p in strong.json()["problems"])
        bad = template("val-3") | {"cases": "not json\n", "prompt": " "}
        probs = c.post("/v1/creator/template-agents", headers=h, json=bad).json()["problems"]
        assert any("line 1" in p for p in probs) and "prompt.md is empty" in probs
        sneaky = template("val-4", runtime={"kind": "code", "entrypoint": "python evil.py"}, tools_requested=["web_search"])
        d = c.post("/v1/creator/template-agents", headers=h, json=sneaky).json()
        files = c.get(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/review", headers=h)
        assert files.status_code == 200
        from app.db import SessionLocal
        from app.models import AgentVersion
        db = SessionLocal()
        m = db.get(AgentVersion, d["version_id"]).manifest_json
        db.close()
        assert m["runtime"] == {"kind": "nocode"} and m["tools_requested"] == []  # server-owned fields

        zh = h | {"content-type": "application/zip"}
        up = lambda data: c.post("/v1/creator/template-agents/upload", headers=zh, content=data)  # noqa: E731
        assert up(zipped(template("zip-ok"), prefix="")).status_code == 201  # files at the zip root work too
        assert "unsafe" in up(zipped(template("zip-1"), extra={"../evil.md": "x"})).json()["detail"]
        assert "unexpected file" in up(zipped(template("zip-2"), extra={"my-agent/main.py": "import os"})).json()["detail"]
        assert up(b"not a zip").json()["detail"] == "not a zip file"
        assert up(b"0" * 1_100_000).status_code == 413
        assert c.post("/v1/creator/template-agents", headers={"authorization": "Bearer tok_sam"},
                      json=template("nope")).status_code == 403  # buyers must switch on the creator role
