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


settings = Settings()

MICRO = 1000  # microcredits per credit (Section 6)


def usd_to_micro(usd: str) -> int:
    """Decimal string dollars -> integer micro-USD (no floats)."""
    from decimal import Decimal
    return int(Decimal(str(usd)) * 1_000_000)


def load_models() -> dict:
    import yaml
    return yaml.safe_load(settings.models_config.read_text())["aliases"]


def credits_to_micro(credits: int | float) -> int:
    return int(round(credits * MICRO))
