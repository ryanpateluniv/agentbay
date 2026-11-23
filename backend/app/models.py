"""Data model from Blueprint Section 12. All money is integer microcredits."""
import secrets
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _id(prefix):
    return mapped_column(String(40), primary_key=True, default=lambda: new_id(prefix))


Money = BigInteger


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id("usr")
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    roles: Mapped[list] = mapped_column(JSON, default=lambda: ["buyer"])
    status: Mapped[str] = mapped_column(String(20), default="active")
    api_token: Mapped[str] = mapped_column(String(80), unique=True, default=lambda: "tok_" + secrets.token_hex(12))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CreatorProfile(Base):
    __tablename__ = "creator_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    payout_account_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_level: Mapped[str] = mapped_column(String(20), default="new")


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = _id("agt")
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(50), default="general")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft live paused delisted
    current_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="hosted")  # hosted | external
    endpoint_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    id: Mapped[str] = _id("agv")
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    semver: Mapped[str] = mapped_column(String(30))
    image_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manifest_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="submitted")  # submitted in_review approved rejected retired
    pricing_model: Mapped[str] = mapped_column(String(30))
    price_credits: Mapped[int] = mapped_column(Integer, default=0)
    limits_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ReviewRun(Base):
    __tablename__ = "review_runs"
    id: Mapped[str] = _id("rvw")
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued running done
    stage: Mapped[str] = mapped_column(String(40), default="intake")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    scores_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_p50: Mapped[int] = mapped_column(Money, default=0)
    cost_p90: Mapped[int] = mapped_column(Money, default=0)
    latency_p50: Mapped[int] = mapped_column(Integer, default=0)  # ms
    latency_p95: Mapped[int] = mapped_column(Integer, default=0)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict)
    report_url: Mapped[str | None] = mapped_column(String(300), nullable=True)


class TestResult(Base):
    __tablename__ = "test_results"
    __test__ = False
    id: Mapped[str] = _id("tst")
    review_run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    case_id: Mapped[str] = mapped_column(String(100))
    run_index: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[int] = mapped_column(Integer)  # 0-100
    cost_microcredits: Mapped[int] = mapped_column(Money, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")


class ReviewFinding(Base):
    __tablename__ = "review_findings"
    id: Mapped[str] = _id("fnd")
    review_run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"))
    severity: Mapped[str] = mapped_column(String(20))  # info low medium high critical
    category: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")


class Listing(Base):
    __tablename__ = "listings"
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    example_tasks: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    badges: Mapped[list] = mapped_column(JSON, default=list)
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    confidence_n: Mapped[int] = mapped_column(Integer, default=0)
    # Aggregated live results (seeded for the demo, updated on each settled session)
    live_stats_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[str] = _id("quo")
    buyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    task_text: Mapped[str] = mapped_column(Text, default="")
    low: Mapped[int] = mapped_column(Money)
    high: Mapped[int] = mapped_column(Money)
    max: Mapped[int] = mapped_column(Money)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    agent_notes: Mapped[dict] = mapped_column(JSON, default=dict)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = _id("ses")
    buyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="created")
    cap: Mapped[int] = mapped_column(Money)
    task_text: Mapped[str] = mapped_column(Text, default="")
    is_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    price: Mapped[int] = mapped_column(Money, default=0)
    cost: Mapped[int] = mapped_column(Money, default=0)
    fee: Mapped[int] = mapped_column(Money, default=0)
    creator_earnings: Mapped[int] = mapped_column(Money, default=0)
    refund_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_message_count: Mapped[int] = mapped_column(Integer, default=0)
    active_ms: Mapped[int] = mapped_column(Integer, default=0)
    work_started: Mapped[bool] = mapped_column(Boolean, default=False)


class SessionMessage(Base):
    """Every event shown on the buyer's stream; also the SSE replay log."""
    __tablename__ = "session_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # buyer agent system
    type: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)


class SessionFile(Base):
    __tablename__ = "session_files"
    id: Mapped[str] = _id("fil")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    storage_ref: Mapped[str] = mapped_column(String(500))
    direction: Mapped[str] = mapped_column(String(5))  # in | out
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SessionToken(Base):
    __tablename__ = "session_tokens"
    id: Mapped[str] = _id("stk")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["llm", "tools"])
    cap: Mapped[int] = mapped_column(Money)  # model+tool cost limit in microcredits
    models_allowed: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    event_id: Mapped[str] = _id("evt")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    agent_version_id: Mapped[str] = mapped_column(String(40))
    creator_id: Mapped[str] = mapped_column(String(40))
    buyer_id: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(30))  # llm_call tool_call ...
    model_alias: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(50), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)  # subset of input_tokens
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)  # subset of output_tokens
    provider_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_cost_micro_usd: Mapped[int] = mapped_column(Money, default=0)
    cost_microcredits: Mapped[int] = mapped_column(Money, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)


class Wallet(Base):
    __tablename__ = "wallets"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    cached_balance: Mapped[int] = mapped_column(Money, default=0)


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("type", "owner_id"),)
    id: Mapped[str] = _id("acc")
    type: Mapped[str] = mapped_column(String(30))
    owner_id: Mapped[str] = mapped_column(String(40))


class LedgerTxn(Base):
    """Groups entries; the unique idempotency key makes every money movement retry-safe."""
    __tablename__ = "ledger_txns"
    id: Mapped[str] = _id("txn")
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    ref_type: Mapped[str] = mapped_column(String(30))
    ref_id: Mapped[str] = mapped_column(String(40))
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_id: Mapped[str] = mapped_column(ForeignKey("ledger_txns.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    direction: Mapped[str] = mapped_column(String(6))  # debit (money leaves) | credit (money arrives)
    amount_microcredits: Mapped[int] = mapped_column(Money)
    ref_type: Mapped[str] = mapped_column(String(30))
    ref_id: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    memo: Mapped[str] = mapped_column(String(100), default="")
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)


class CreditPurchase(Base):
    __tablename__ = "credit_purchases"
    id: Mapped[str] = _id("pur")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    provider_ref: Mapped[str] = mapped_column(String(100))
    amount_usd_cents: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="paid")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = _id("sub")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    plan: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    renews_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Payout(Base):
    __tablename__ = "payouts"
    id: Mapped[str] = _id("pay")
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default="simulated")
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Rating(Base):
    __tablename__ = "ratings"
    id: Mapped[str] = _id("rat")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), unique=True)
    buyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    stars: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    creator_reply: Mapped[str | None] = mapped_column(Text, nullable=True)


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = _id("dsp")
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    refund_amount: Mapped[int] = mapped_column(Money, default=0)


class ModelPrice(Base):
    """Prices are integer micro-USD per 1M tokens (0.15 $/M -> 150_000)."""
    __tablename__ = "model_prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(40), index=True)
    provider_model: Mapped[str] = mapped_column(String(100))
    price_in: Mapped[int] = mapped_column(Money)
    price_out: Mapped[int] = mapped_column(Money)
    price_cached_in: Mapped[int | None] = mapped_column(Money, nullable=True)  # None -> same as price_in
    drop_params: Mapped[list] = mapped_column(JSON, default=list)  # request params this model rejects
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=now)


class ToolPrice(Base):
    __tablename__ = "tool_prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String(50), index=True)
    unit: Mapped[str] = mapped_column(String(20), default="call")
    price: Mapped[int] = mapped_column(Money)  # microcredits per unit
    effective_from: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlatformSetting(Base):
    """Fee rate and markup editable via PUT /v1/admin/fees."""
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now)
