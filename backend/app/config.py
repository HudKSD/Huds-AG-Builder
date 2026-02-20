from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    APP_HOST: str = '0.0.0.0'
    APP_PORT: int = 8000
    APP_SECRET: str = 'change_me'
    PUBLIC_BASE_URL: str = 'http://localhost:8000'
    LOG_LEVEL: str = 'INFO'

    ES_URL: str = 'https://localhost:9200'
    ES_USERNAME: str = 'elastic'
    ES_PASSWORD: str = 'changeme'
    ES_VERIFY_SSL: bool = True
    ES_CA_CERT_PATH: str = '/certs/ca.crt'
    ES_INDEX_ALLOWLIST: str = 'logs-*'
    ES_REQUEST_TIMEOUT: int = 30

    SCHEMA_REFRESH_SECONDS: int = 900
    SCHEMA_CACHE_TTL_SECONDS: int = 3600
    SCHEMA_MAX_FIELDS: int = 8000
    SCHEMA_TOP_FIELDS_IN_CONTEXT: int = 250

    DB_URL: str = 'sqlite+aiosqlite:///./app.db'
    REDIS_URL: str = 'redis://redis:6379/0'

    LLM_BASE_URL: str = 'https://api.openai.com/v1'
    LLM_API_KEY: str = ''
    LLM_MODEL: str = 'gpt-4o-mini'
    LLM_TIMEOUT_SECONDS: int = 60
    LLM_MAX_OUTPUT_TOKENS: int = 900
    LLM_USE_STRUCTURED_OUTPUTS: bool = True

    AGENT_MAX_TOOL_STEPS: int = 8
    AGENT_DEFAULT_TIME_WINDOW_HOURS: int = 24
    AGENT_MAX_CONTEXT_TOKENS: int = 120000
    AGENT_CONTEXT_RESERVE_FOR_ANSWER: int = 1800

    SECURITY_ENABLE_AUTH: bool = True
    SECURITY_JWT_SECRET: str = 'change_me'
    SECURITY_RBAC_DEFAULT_ROLE: str = 'analyst'
    RATE_LIMIT_RPM: int = 60

    SAFE_MODE_MAX_TIME_RANGE_DAYS: int = 30
    SAFE_MODE_MAX_DOCS: int = 200
    SAFE_MODE_MAX_AGG_BUCKETS: int = 50
    SAFE_MODE_DISALLOW_QUERY_TYPES: str = 'script,percolate,regexp,query_string'
    SAFE_MODE_DISALLOW_FIELDS: str = '_source,_all'


settings = Settings()


def allowlist_patterns() -> list[str]:
    return [x.strip() for x in settings.ES_INDEX_ALLOWLIST.split(',') if x.strip()]
