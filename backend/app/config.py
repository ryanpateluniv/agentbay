"""Runtime configuration, read from environment (and .env at the repo root)."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
load_dotenv(REPO_ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Settings:
    database_url = _env("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'data' / 'marketplace.db'}")
    data_dir = Path(_env("DATA_DIR", str(BACKEND_DIR / "data")))
    llm_mode = _env("LLM_MODE", "fake")  # fake | real
    llm_upstream_base_url = _env("LLM_UPSTREAM_BASE_URL", "https://api.openai.com/v1")
    # OPENAI_API_KEY wins; LLM_UPSTREAM_API_KEY is kept for other OpenAI-compatible upstreams. Never logged.
    llm_upstream_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_UPSTREAM_API_KEY", "")
    llm_timeout_s = float(_env("LLM_TIMEOUT_S", "60"))
    llm_max_retries = int(_env("LLM_MAX_RETRIES", "3"))
    # Hard safety caps on real provider spend (USD, decimal strings). Only enforced in real mode.
    real_max_usd_per_session = _env("REAL_MAX_USD_PER_SESSION", "0.25")
    real_max_usd_per_run = _env("REAL_MAX_USD_PER_RUN", "2.00")
    models_config = Path(_env("MODELS_CONFIG", str(BACKEND_DIR / "config" / "models.yaml")))
    runtime_mode = _env("RUNTIME_MODE", "subprocess")  # subprocess | docker
    public_base_url = _env("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    fee_bps = int(_env("PLATFORM_FEE_BPS", "2000"))  # f = 20%
    markup_bps = int(_env("PLATFORM_MARKUP_BPS", "1000"))  # m = 10%
    hmac_secret_external = _env("HMAC_SECRET_EXTERNAL", "dev-external-secret")
    cors_origins = _env("CORS_ORIGINS", "*").split(",")
    quote_lock_seconds = 600
    max_session_minutes_platform = 60
    review_runs_per_case = int(_env("REVIEW_RUNS_PER_CASE", "2"))
    sdk_dir = REPO_ROOT / "sdk" / "python"
    agents_dir = Path(_env("AGENTS_DIR", str(REPO_ROOT / "agents")))
    review_on_boot = _env("REVIEW_ON_BOOT", "1") == "1"
    db_pool_size = int(_env("DB_POOL_SIZE", "20"))
    db_max_overflow = int(_env("DB_MAX_OVERFLOW", "30"))
    db_pool_timeout = int(_env("DB_POOL_TIMEOUT", "60"))
    review_judge = _env("REVIEW_JUDGE", "auto")  # auto: LLM judge in real mode, heuristic in fake | heuristic
    judge_max_tokens = int(_env("JUDGE_MAX_TOKENS", "2000"))  # reasoning tokens count against this
    review_concurrency = int(_env("REVIEW_CONCURRENCY", "3"))  # reviews running at once
    review_case_concurrency = int(_env("REVIEW_CASE_CONCURRENCY", "3"))  # test-case sessions at once per review
    trial_credits = int(_env("TRIAL_CREDITS", "500"))  # granted once to every new account
    session_days = int(_env("SESSION_DAYS", "30"))  # review versions left in "submitted" (seeded scam agent)


settings = Settings()

MICRO = 1000  # microcredits per credit (Section 6)


def usd_to_micro(usd: str) -> int:
    """Decimal string dollars -> integer micro-USD (no floats)."""
    from decimal import Decimal
    return int(Decimal(str(usd)) * 1_000_000)


def load_models() -> dict:
    import yaml
    return yaml.safe_load(settings.models_config.read_text())["aliases"]


def platform_alias(use: str, default: str = "fast") -> str:
    """Alias the platform itself uses for `use` (e.g. judge_alias), from config/models.yaml."""
    import yaml
    return (yaml.safe_load(settings.models_config.read_text()).get("platform") or {}).get(use, default)


def credits_to_micro(credits: int | float) -> int:
    return int(round(credits * MICRO))
