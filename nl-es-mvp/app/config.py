import os
from dataclasses import dataclass, field
from typing import List, Optional

def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return int(v.strip())
    except ValueError:
        return default

def _get_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return float(v.strip())
    except ValueError:
        return default

def _get_list(name: str, default: List[str]) -> List[str]:
    v = os.getenv(name)
    if not v:
        return default
    return [x.strip() for x in v.split(",") if x.strip()]

@dataclass(frozen=True)
class Settings:
    # App
    log_level: str = os.getenv("APP_LOG_LEVEL", "INFO")
    row_limit: int = _get_int("ROW_LIMIT", 50)
    default_time_from: str = os.getenv("DEFAULT_TIME_FROM", "now-24h")
    default_time_to: str = os.getenv("DEFAULT_TIME_TO", "now")
    max_agent_steps: int = _get_int("MAX_AGENT_STEPS", 8)
    conversation_max_turns: int = _get_int("CONVERSATION_MAX_TURNS", 30)

    # ES
    es_url: str = os.getenv("ES_URL", "https://localhost:9200")
    es_username: str = os.getenv("ES_USERNAME", "elastic")
    es_password: str = os.getenv("ES_PASSWORD", "")
    es_verify_certs: bool = _get_bool("ES_VERIFY_CERTS", True)
    es_ca_certs: Optional[str] = os.getenv("ES_CA_CERTS") or None
    es_ssl_assert_fingerprint: Optional[str] = os.getenv("ES_SSL_ASSERT_FINGERPRINT") or None
    es_request_timeout: int = _get_int("ES_REQUEST_TIMEOUT", 20)

    # ✅ FIX: use default_factory for list
    es_allowed_patterns: List[str] = field(
        default_factory=lambda: _get_list("ES_ALLOWED_PATTERNS", ["logs-*"])
    )

    # LLM (OpenAI compatible)
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_temperature: float = _get_float("LLM_TEMPERATURE", 0.2)
    llm_max_tokens: int = _get_int("LLM_MAX_TOKENS", 700)

