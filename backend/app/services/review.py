"""Review pipeline (Section 8). Filled in at build step 7."""
from sqlalchemy.orm import Session as DB

from ..db import SessionLocal
from ..models import Agent, AgentVersion, ReviewRun


def start_review(version_id: str) -> str:
    db = SessionLocal()
    run = ReviewRun(agent_version_id=version_id, status="queued", stage="intake")
    db.add(run)
    db.get(AgentVersion, version_id).status = "in_review"
    db.commit()
    return run.id


def publish_version(db: DB, agent: Agent, ver: AgentVersion):
    agent.current_version_id = ver.id
    agent.status = "live"
    db.commit()
