"""No-code agents: a folder of agent.yaml + prompt.md + cases.jsonl, run by marketplace_sdk.nocode.

A manifest is no-code when `runtime.kind: nocode`. Its entrypoint is fixed (creators can't run their own code),
its test cases default to cases.jsonl, and only the fast/balanced aliases are allowed. Folders like that under
AGENTS_DIR are discovered at startup and submitted for review (names starting with "_" or "." are skipped, so
agents/_template stays an example)."""
import io
import json
import logging
import zipfile
from pathlib import Path, PurePosixPath

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
    errors += check_models(manifest)
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


# ---------- creator publishing (POST /v1/creator/template-agents[/upload]) ----------

UPLOAD_ROOT = settings.data_dir / "creator_agents"
MAX_ZIP_BYTES, MAX_UNZIPPED_BYTES, MAX_ZIP_FILES = 1_000_000, 2_000_000, 20
TEXT_FILES = {"agent.yaml", "prompt.md", "cases.jsonl", "README.md"}


class TemplateError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def needs_admin(ver: AgentVersion) -> bool:
    """Creator-published template versions go live only after an admin approves them."""
    try:
        return Path(ver.source_path or "").resolve().is_relative_to(UPLOAD_ROOT.resolve())
    except OSError:
        return False


def normalize(manifest: dict, user: User) -> dict:
    """Server-owned fields: a creator can't pick an entrypoint, tools or secrets for a no-code agent."""
    m = dict(manifest or {})
    m["runtime"] = {"kind": "nocode"}
    m["nocode"] = {k: v for k, v in (m.get("nocode") or {}).items() if k != "prompt_file"}  # always prompt.md
    m["review"] = {"test_cases": "cases.jsonl"}
    m["tools_requested"], m["secrets"] = [], []
    m.setdefault("models_allowed", ["fast"])
    m.setdefault("support_contact", user.email)
    return m


def parse_cases(cases) -> tuple[list[dict], list[str]]:
    rows, problems = [], []
    lines = cases.splitlines() if isinstance(cases, str) else [json.dumps(c) for c in cases or []]
    for i, line in enumerate(x for x in lines if x.strip()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"cases.jsonl line {i + 1}: not valid JSON")
            continue
        if not isinstance(row, dict) or not isinstance(row.get("task"), str):
            problems.append(f"cases.jsonl line {i + 1}: needs a \"task\" string")
            continue
        if not isinstance(row.get("answers", []), list) or not isinstance(row.get("expect_contains", []), list):
            problems.append(f"cases.jsonl line {i + 1}: \"answers\" and \"expect_contains\" must be lists")
            continue
        rows.append(row)
    if not rows and not problems:
        problems.append("add at least one test case to cases.jsonl")
    if len(rows) > 9:
        problems.append("cases.jsonl: at most 9 cases")
    return rows, problems


def save_template(db: DB, user: User, manifest: dict, prompt: str, cases, lint) -> AgentVersion:
    """Validate and store a no-code agent as a draft version of a new or existing agent owned by `user`."""
    m = normalize(manifest, user)
    rows, problems = parse_cases(cases)
    if not (prompt or "").strip():
        problems.append("prompt.md is empty")
    if len(prompt or "") > 50_000:
        problems.append("prompt.md is over 50,000 characters")
    problems = lint(db, m) + [p for p in check_models(m)] + problems
    if problems:
        raise TemplateError(problems)
    agent = db.scalar(select(Agent).where(Agent.slug == m["name"]))
    if agent is not None and agent.creator_id != user.id:
        raise TemplateError([f"name {m['name']!r} is taken by another creator"])
    if agent is None:
        agent = Agent(creator_id=user.id, slug=m["name"], category=m.get("category", "general"), mode="hosted")
        db.add(agent)
        db.flush()
    semver = str(m["version"])
    if db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.semver == semver)):
        raise TemplateError([f"version {semver} already exists for {m['name']}; bump `version`"])
    pricing = m.get("pricing") or {}
    ver = AgentVersion(agent_id=agent.id, semver=semver, manifest_json=m, status="draft",
                       pricing_model=str(pricing.get("model", "per_task")),
                       price_credits=int(pricing.get("price_credits", 0) or 0), limits_json=m.get("limits") or {})
    db.add(ver)
    db.flush()
    folder = UPLOAD_ROOT / agent.id / ver.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agent.yaml").write_text(yaml.safe_dump(m, sort_keys=False, allow_unicode=True))
    (folder / prompt_file(m)).write_text(prompt)
    (folder / "cases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ver.source_path = str(folder)
    db.commit()
    return ver


def check_models(m: dict) -> list[str]:
    bad = [a for a in m.get("models_allowed") or [] if a not in ALIASES]
    return [f"no-code agents can use the fast and balanced aliases only, not {bad}"] if bad else []


def read_zip(data: bytes) -> tuple[dict, str, str]:
    """(manifest, prompt, cases_text) from a zipped template folder. Text files only, no paths outside it."""
    if len(data) > MAX_ZIP_BYTES:
        raise TemplateError([f"zip is over {MAX_ZIP_BYTES // 1_000_000} MB"])
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = [i for i in z.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
                 and PurePosixPath(i.filename).name not in (".DS_Store",)]
    except zipfile.BadZipFile:
        raise TemplateError(["not a zip file"])
    if len(infos) > MAX_ZIP_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED_BYTES:
        raise TemplateError(["zip has too many files or is too large unzipped"])
    paths = [PurePosixPath(i.filename) for i in infos]
    if any(p.is_absolute() or ".." in p.parts or (i.external_attr >> 16) & 0o170000 == 0o120000
           for p, i in zip(paths, infos)):
        raise TemplateError(["zip contains unsafe paths or links"])
    tops = {p.parts[0] for p in paths if len(p.parts) > 1}
    strip = 1 if len(tops) == 1 and all(len(p.parts) > 1 for p in paths) else 0  # folder zipped as a whole
    files = {}
    for p, i in zip(paths, infos):
        rel = "/".join(p.parts[strip:])
        if rel not in TEXT_FILES:
            raise TemplateError([f"unexpected file {rel!r}: a no-code agent is agent.yaml, prompt.md and cases.jsonl"])
        try:
            files[rel] = z.read(i).decode("utf-8")
        except UnicodeDecodeError:
            raise TemplateError([f"{rel} is not UTF-8 text"])
    missing = [f for f in ("agent.yaml", "prompt.md", "cases.jsonl") if f not in files]
    if missing:
        raise TemplateError([f"zip is missing {', '.join(missing)}"])
    try:
        manifest = yaml.safe_load(files["agent.yaml"]) or {}
    except yaml.YAMLError as e:
        raise TemplateError([f"agent.yaml is not valid YAML: {e}"])
    if not isinstance(manifest, dict):
        raise TemplateError(["agent.yaml must be a mapping"])
    return manifest, files["prompt.md"], files["cases.jsonl"]
