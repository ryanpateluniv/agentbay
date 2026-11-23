"""Real-mode judge robustness, approvals that survive an LLM_MODE switch, and the offline seed review."""
import asyncio
import shutil

import pytest
import yaml

from app.config import REPO_ROOT, settings

OUT = {"status": "settled", "result": "A clear, complete answer about the Lisbon itinerary and budget.", "files_text": ""}


@pytest.fixture
def real_judge(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "real")
    monkeypatch.setattr(settings, "review_judge", "auto")


def test_parse_judge_tolerates_fences_and_prose():
    from app.services.review import parse_judge
    assert parse_judge('```json\n{"score": 82, "why": "ok"}\n```') == 82
    assert parse_judge('Here you go: {"why": "uses {braces}", "score": "91"} thanks') == 91
    assert parse_judge('{"score": 140}') == 100
    for bad in ("", "no json here", '{"why": "missing score"}'):
        with pytest.raises(ValueError):
            parse_judge(bad)


def test_judge_parse_failure_retries_then_falls_back_to_heuristic(real_judge, monkeypatch):
    from app.services import review
    calls = []

    async def broken(task, blob):
        calls.append(task)
        raise ValueError("Expecting value: line 1 column 1 (char 0)")
    monkeypatch.setattr(review, "_llm_judge", broken)
    passed, score, detail = asyncio.run(review.judge("plan Lisbon", OUT, ["lisbon"]))
    assert (passed, score) == (True, 85) and "heuristic" in detail and len(calls) == 2  # never rejected or 0

    async def ok(task, blob):
        return 72
    monkeypatch.setattr(review, "_llm_judge", ok)
    assert asyncio.run(review.judge("plan Lisbon", OUT, [])) == (True, 72, "ok")
    monkeypatch.setattr(settings, "review_judge", "heuristic")
    monkeypatch.setattr(review, "_llm_judge", broken)
    assert asyncio.run(review.judge("plan Lisbon", OUT, []))[1] == 85 and len(calls) == 2  # not even tried


def test_llm_judge_empty_reply_then_fenced_json(db, real_judge, monkeypatch):
    from app.services import review, upstream
    payloads = []
    replies = iter(["", '```json\n{"score": 77, "why": "fine"}\n```'])

    async def fake_complete(payload):
        payloads.append(payload)
        return {"id": f"r{len(payloads)}", "choices": [{"message": {"content": next(replies)}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 900}}
    monkeypatch.setattr(upstream, "complete", fake_complete)
    assert asyncio.run(review.judge("plan Lisbon", OUT, [])) == (True, 77, "ok")
    assert len(payloads) == 2
    assert payloads[0]["max_completion_tokens"] == settings.judge_max_tokens >= 2000  # room for reasoning tokens
    assert payloads[0]["response_format"] == {"type": "json_object"}


def test_approved_versions_survive_a_mode_switch(db, monkeypatch, tmp_path):
    from app.main import startup_queue
    from app.models import Agent, AgentVersion, ReviewRun
    from app.services import catalog, nocode, review
    live_before = sorted(a.slug for a in catalog.live_agents(db))
    folder = tmp_path / "mode-notes"
    shutil.copytree(REPO_ROOT / "agents" / "_template", folder)
    m = yaml.safe_load((folder / "agent.yaml").read_text()) | {"name": "mode-notes"}
    (folder / "agent.yaml").write_text(yaml.safe_dump(m))
    [vid] = nocode.discover(db, tmp_path)
    ver = db.get(AgentVersion, vid)
    ver.status = "approved"
    db.add(ReviewRun(agent_version_id=vid, status="done", stage="decision", decision="approved", scores_json={}))
    db.commit()
    review.publish_version(db, db.get(Agent, ver.agent_id), ver)
    # an interrupted review (server stopped mid-run) is re-queued; a submitted one is queued
    db.add(ReviewRun(agent_version_id="agv_trip_planner_1", status="running", stage="build"))
    db.get(AgentVersion, "agv_trip_planner_1").status = "in_review"
    db.commit()
    for mode in ("real", "fake"):
        monkeypatch.setattr(settings, "llm_mode", mode)
        assert nocode.discover(db, tmp_path) == []  # unchanged folder: no new version, no re-review
        queued = startup_queue(db)
        assert vid not in queued and not set(queued) - {"agv_trip_planner_1"}
        db.get(AgentVersion, "agv_trip_planner_1").status = "approved"
        db.commit()
    db.expire_all()
    assert db.get(AgentVersion, vid).status == "approved"
    assert sorted(a.slug for a in catalog.live_agents(db)) == sorted(live_before + ["mode-notes"])


def test_seed_approves_policy_held_repo_agents_only(db):
    from app.models import AgentVersion, ReviewRun
    from app.seed import approve_held_repo_agents
    from app.services import catalog
    for vid, decision in (("agv_trip_planner_1", "pending_human_review"), ("agv_quick_summarizer_1", "rejected")):
        db.get(AgentVersion, vid).status = "in_review"
        db.add(ReviewRun(agent_version_id=vid, status="done", stage="decision", decision=decision, scores_json={}))
    db.commit()
    assert approve_held_repo_agents(db) == ["trip-planner"]
    assert catalog.latest_review(db, "agv_trip_planner_1").decision == "approved"
    assert db.get(AgentVersion, "agv_quick_summarizer_1").status == "in_review"


def test_offline_seed_review_lists_repo_agents_in_any_mode(db, monkeypatch, tmp_path):
    from app import seed
    from app.models import AgentVersion
    code = tmp_path / "echo-repo"
    shutil.copytree(REPO_ROOT / "sdk" / "python" / "examples" / "echo_agent", code)
    m = yaml.safe_load((code / "agent.yaml").read_text())
    m["name"] = "echo-repo"
    m["review"] = {"test_cases": "tests/cases.jsonl", "support_contact": "x@example.com"}
    m.pop("support_contact")  # nested like the teammate manifests: the seed hoists it
    (code / "agent.yaml").write_text(yaml.safe_dump(m))
    nc = tmp_path / "nocode-repo"
    shutil.copytree(REPO_ROOT / "agents" / "_template", nc)
    (nc / "agent.yaml").write_text(yaml.safe_dump(yaml.safe_load((nc / "agent.yaml").read_text()) | {"name": "nocode-repo"}))
    for attr in ("agents_dir", "llm_mode", "review_judge", "review_on_boot", "public_base_url", "review_concurrency"):
        monkeypatch.setattr(settings, attr, getattr(settings, attr))  # review_offline changes these; restore after
    monkeypatch.setattr(settings, "agents_dir", tmp_path)
    monkeypatch.setattr(settings, "llm_mode", "real")  # the seed review must not depend on the mode (or cost money)
    assert seed.register_repo_agents(db, set()) == ["agv_echo_repo_1"]
    db.commit()
    assert db.get(AgentVersion, "agv_echo_repo_1").manifest_json["support_contact"] == "x@example.com"
    results = seed.review_offline(timeout_s=300)
    assert results["echo-repo"] == "listed" and results["nocode-repo"] == "listed"
    assert settings.llm_mode == "fake" and settings.review_judge == "heuristic"
