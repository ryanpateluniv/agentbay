"""Request/response models. These define contracts/openapi.yaml. All money: integer microcredits (1 credit = 1000)."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    error: str = Field(description="machine code: insufficient_credits, conflict, not_found, budget_exhausted, ...")
    detail: str = ""


# ---------- discovery ----------

class SearchFilters(BaseModel):
    category: str | None = None
    input_type: str | None = None
    output_type: str | None = None
    max_price_microcredits: int | None = None
    max_minutes: int | None = None
    verified_only: bool = False


class SearchRequest(BaseModel):
    task: str = Field(examples=["Plan a 5-day trip to Tokyo under $2,000"])
    filters: SearchFilters = SearchFilters()
    limit: int = 6


class PriceRange(BaseModel):
    low_microcredits: int
    high_microcredits: int
    max_microcredits: int
    pricing_model: str


class AgentCard(BaseModel):
    id: str
    slug: str
    title: str
    summary: str
    category: str
    tags: list[str]
    mode: Literal["hosted", "external"]
    badges: list[str]
    confidence_score: int
    confidence_n: int = Field(description="sample size shown next to the score")
    confidence_lower_bound: float
    price: PriceRange
    duration_p50_s: int
    duration_p95_s: int
    success_rate: float | None
    rating_avg: float | None
    rating_n: int
    relevance: float = 0.0
    inputs: list[str] = []
    outputs: list[str] = []
    example_tasks: list[str] = []
    description: str = ""
    creator_name: str = ""


class SearchResponse(BaseModel):
    task: str
    results: list[AgentCard]


class ReportCardSummary(BaseModel):
    decision: str | None
    scores: dict[str, float]
    cost_p50_microcredits: int
    cost_p90_microcredits: int
    latency_p50_ms: int
    latency_p95_ms: int
    findings: list[dict[str, Any]]
    reviewed_at: datetime | None
    stages: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []


class AgentDetail(AgentCard):
    description: str
    example_tasks: list[str]
    current_version: dict[str, Any]
    report_card: ReportCardSummary | None
    tools_requested: list[str]
    models_allowed: list[str]
    ratings: list[dict[str, Any]]
    live_stats: dict[str, Any] = {}


class CompareRequest(BaseModel):
    agent_ids: list[str] = Field(max_length=3)
    task: str = ""


class CompareResponse(BaseModel):
    agents: list[AgentDetail]
    highlights: dict[str, str | None] = Field(description="best_value / highest_confidence / fastest -> agent id")


class QuoteRequest(BaseModel):
    agent_id: str
    task: str


class QuoteOut(BaseModel):
    id: str
    agent_id: str
    agent_version_id: str
    low_microcredits: int
    high_microcredits: int
    max_microcredits: int
    suggested_cap_microcredits: int = Field(description="default cap = quoted p90")
    pricing_model: str
    expires_at: datetime
    agent_notes: dict[str, Any] = {}
    low_credits: float | None = None
    high_credits: float | None = None
    maximum: float | None = Field(None, description="max in credits (display convenience)")


# ---------- sessions ----------

class SessionCreate(BaseModel):
    quote_id: str | None = Field(None, description="required unless agent_id + task are given (then a quote is made)")
    cap_microcredits: int | None = Field(None, description="defaults to the quote's p90")
    agent_id: str | None = Field(None, description="frontend convenience: quote and start in one call")
    task: str | None = None
    cap_credits: float | None = Field(None, description="frontend convenience: cap in credits (converted to microcredits)")
    files: list[dict[str, str]] = Field(default=[], description="[{name, content_base64}] input files")


class SpendOut(BaseModel):
    spent_microcredits: int = Field(description="platform cost C so far (model + tools)")
    projected_charge_microcredits: int = Field(description="what the buyer would pay if it ended now")
    cap_microcredits: int
    llm_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int


class SessionOut(BaseModel):
    id: str
    status: str
    agent_id: str
    agent_version_id: str
    buyer_id: str
    task: str
    cap_microcredits: int
    spend: SpendOut
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    settled_at: datetime | None
    result_text: str | None
    files: list[dict[str, Any]]
    pending_question: dict[str, Any] | None
    session_id: str | None = Field(None, description="alias of id (returned on create)")


class BuyerMessage(BaseModel):
    text: str
    reply_to: str | None = Field(None, description="id of the ask_user/confirm event being answered")


class ConfirmIn(BaseModel):
    confirm_id: str
    approved: bool


class ExtendIn(BaseModel):
    add_microcredits: int = Field(gt=0)


class RatingIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str = ""


class DisputeIn(BaseModel):
    reason: str


class ReceiptLine(BaseModel):
    label: str
    amount_microcredits: int


class Receipt(BaseModel):
    session_id: str
    agent_title: str
    status: str
    pricing_model: str
    cap_microcredits: int
    price_microcredits: int = Field(description="P, charged to the buyer")
    cost_microcredits: int = Field(description="C, platform model+tool cost")
    fee_microcredits: int = Field(description="F, platform fee")
    creator_earnings_microcredits: int = Field(description="E = max(0, P - F - C)")
    released_microcredits: int = Field(description="unused hold returned to the wallet")
    refund_reason: str | None
    usage: dict[str, Any]
    lines: list[ReceiptLine]
    ledger: list[dict[str, Any]]
    # display conveniences in credits
    price_credits: float | None = None
    cost_credits: float | None = None
    fee_credits: float | None = None
    earnings_credits: float | None = None
    cap_credits: float | None = None
    released_credits: float | None = None


class WalletOut(BaseModel):
    user_id: str
    balance_microcredits: int
    held_microcredits: int = Field(description="currently in escrow for running sessions")
    history: list[dict[str, Any]]
    # display conveniences (credits, may be fractional; money of record is the microcredit fields)
    user: str = ""
    email: str = ""
    balance_credits: float = 0
    granted_credits: float = Field(0, description="all credits ever added (promo + purchases)")
    used_credits: float = Field(0, description="granted - balance - held")


class TopupIn(BaseModel):
    pack: Literal["S", "M", "L"] | None = None
    credits: int | None = Field(None, gt=0, le=10_000, description="mock top-up of an exact credit amount (demo)")


class SubscriptionIn(BaseModel):
    plan: Literal["plus", "pro", "none"]


# ---------- creator ----------

class AgentCreate(BaseModel):
    slug: str
    category: str = "general"
    mode: Literal["hosted", "external"] = "hosted"
    endpoint_url: str | None = None


class VersionSubmit(BaseModel):
    source_path: str | None = Field(None, description="local path or repo dir containing agent.yaml (MVP: local path)")
    repo_url: str | None = None
    image_ref: str | None = None
    manifest: dict[str, Any] | None = Field(None, description="overrides agent.yaml if given")


class ListingPatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    example_tasks: list[str] | None = None
    price_credits: int | None = None


class PayoutIn(BaseModel):
    amount_microcredits: int = Field(gt=0)


# ---------- admin ----------

class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected", "changes_requested"]
    notes: str = ""


class ModelPriceIn(BaseModel):
    alias: str
    provider_model: str
    price_in_micro_usd_per_mtok: int
    price_out_micro_usd_per_mtok: int


class ToolPriceIn(BaseModel):
    tool: str
    unit: str = "call"
    price_microcredits: int


class PricesIn(BaseModel):
    models: list[ModelPriceIn] = []
    tools: list[ToolPriceIn] = []


class FeesIn(BaseModel):
    fee_bps: int | None = Field(None, ge=0, le=10000, description="platform fee f in basis points (2000 = 20%)")
    markup_bps: int | None = Field(None, ge=0, le=10000, description="cost markup m in basis points")


class RefundIn(BaseModel):
    reason: str = "admin refund"


class ResolveIn(BaseModel):
    resolution: Literal["refund", "reject"]
