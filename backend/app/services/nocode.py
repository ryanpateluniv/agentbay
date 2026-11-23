"""No-code agents: a folder of agent.yaml + prompt.md + cases.jsonl, run by marketplace_sdk.nocode.

A manifest is no-code when `runtime.kind: nocode`. Its entrypoint is fixed (creators can't run their own code),
its test cases default to cases.jsonl, and only the fast/balanced aliases are allowed. Folders like that under
AGENTS_DIR are discovered at startup and submitted for review (names starting with "_" or "." are skipped, so
agents/_template stays an example)."""
import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..config import settings
from ..models import Agent, AgentVersion, User

log = logging.getLogger(__name__)
ENTRYPOINT = "python -m marketplace_sdk.nocode"
ALIASES = ("fast", "balanced")


def is_nocode(manifest: dict) -> bool:
    return (manifest.get("runtime") or {}).get("kind") == "nocode"


def entrypoint(manifest: dict) -> str:
    if is_nocode(manifest):
        return ENTRYPOINT
    return (manifest.get("runtime") or {}).get("entrypoint", "python main.py")


def cases_path(manifest: dict) -> str:
    return (manifest.get("review") or {}).get("test_cases") or ("cases.jsonl" if is_nocode(manifest) else
                                                                  "tests/cases.jsonl")


def prompt_file(manifest: dict) -> str:
    return (manifest.get("nocode") or {}).get("prompt_file", "prompt.md")


def check_folder(src: Path, manifest: dict) -> list[str]:
    """Problems that stop a no-code folder from running (used by review's build stage and the publish API)."""
    errors = []
    if not (src / prompt_file(manifest)).is_file():
        errors.append(f"{prompt_file(manifest)} is missing")
    elif not (src / prompt_file(manifest)).read_text(errors="ignore").strip():
        errors.append(f"{prompt_file(manifest)} is empty")
    bad = [a for a in manifest.get("models_allowed") or [] if a not in ALIASES]
    if bad:
        errors.append(f"no-code agents can use the fast and balanced aliases only, not {bad}")
    if manifest.get("tools_requested"):
        errors.append("no-code agents can't request tools yet")
    return errors


def _owner(db: DB, manifest: dict) -> User | None:
    contact = str(manifest.get("support_contact") or "").lower()
    user = db.scalar(select(User).where(User.email == contact)) if "@" in contact else None
    if user and "creator" in user.roles:
        return user
    return db.get(User, "usr_riya") or next((u for u in db.scalars(select(User)) if "creator" in u.roles), None)


def discover(db: DB, root: Path | None = None) -> list[str]:
    """Register no-code folders under AGENTS_DIR that aren't in the database yet (new agent, or a new version
    number of an existing one) as submitted versions. Returns the new version ids."""
    root = root or settings.agents_dir
    created = []
    if not root.is_dir():
        return created
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        if not (folder / "agent.yaml").is_file() or (folder / "main.py").exists():
            continue
        try:
            m = yaml.safe_load((folder / "agent.yaml").read_text()) or {}
        except yaml.YAMLError as e:
            log.warning("skipping %s: bad agent.yaml (%s)", folder.name, e)
            continue
        if not is_nocode(m) or not m.get("name"):
            continue
        agent = db.scalar(select(Agent).where(Agent.slug == m["name"]))
        if agent is None:
            owner = _owner(db, m)
            if owner is None:
                log.warning("skipping %s: no creator account to own it", folder.name)
                continue
            agent = Agent(creator_id=owner.id, slug=m["name"], category=m.get("category", "general"), mode="hosted")
            db.add(agent)
            db.flush()
        semver = str(m.get("version", "0.0.0"))
        if db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.semver == semver)):
            continue
        pricing = m.get("pricing") or {}
        ver = AgentVersion(agent_id=agent.id, semver=semver, source_path=str(folder), manifest_json=m,
                           status="submitted", pricing_model=str(pricing.get("model", "per_task")),
                           price_credits=int(pricing.get("price_credits", 0) or 0), limits_json=m.get("limits") or {})
        db.add(ver)
        db.flush()
        created.append(ver.id)
    db.commit()
    return created
