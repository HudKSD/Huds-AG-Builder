import asyncio
import time
from fnmatch import fnmatch
from .client import get_es_client
from ..config import settings, allowlist_patterns
from ..metrics import schema_refresh_duration_seconds

SCHEMA_CACHE: dict[str, dict] = {}
CACHE_TS: dict[str, float] = {}


def _detect_keyword_variant(fields: dict, field_name: str) -> str | None:
    kv = f'{field_name}.keyword'
    return kv if kv in fields else None


def _detect_time_field(fields: dict) -> str | None:
    candidates = [n for n, v in fields.items() if v.get('type') in {'date', 'date_nanos'}]
    for preferred in ('@timestamp', 'timestamp', 'event.created', 'event.ingested'):
        if preferred in candidates:
            return preferred
    return candidates[0] if candidates else None


async def get_schema(pattern: str) -> dict:
    now = time.time()
    if pattern in SCHEMA_CACHE and now - CACHE_TS.get(pattern, 0) < settings.SCHEMA_CACHE_TTL_SECONDS:
        return SCHEMA_CACHE[pattern]

    es = get_es_client()
    mappings = await es.indices.get_mapping(index=pattern)
    field_caps = await es.field_caps(index=pattern, fields='*')
    fields = {}
    for fname, spec in field_caps.get('fields', {}).items():
        first_type = list(spec.values())[0]
        fields[fname] = {
            'name': fname,
            'type': first_type.get('type', 'unknown'),
            'searchable': bool(first_type.get('searchable', False)),
            'aggregatable': bool(first_type.get('aggregatable', False)),
        }
    field_rows = []
    for name, row in list(fields.items())[: settings.SCHEMA_MAX_FIELDS]:
        kv = _detect_keyword_variant(fields, name)
        if kv:
            row['keyword_variant'] = kv
        field_rows.append(row)
    schema = {
        'pattern': pattern,
        'resolved_indices': list(mappings.keys()),
        'time_field': _detect_time_field(fields),
        'fields': field_rows,
        'field_suggestions_hint': 'Use aggregatable fields for terms and keyword variants for group-by',
    }
    SCHEMA_CACHE[pattern] = schema
    CACHE_TS[pattern] = now
    await es.close()
    return schema


async def refresh_schema_job():
    start = time.time()
    for p in allowlist_patterns():
        try:
            await get_schema(p)
        except Exception:
            pass
    schema_refresh_duration_seconds.observe(time.time() - start)


def pattern_allowed(pattern: str) -> bool:
    return any(fnmatch(pattern, p) for p in allowlist_patterns())


async def scheduler_loop():
    while True:
        await refresh_schema_job()
        await asyncio.sleep(settings.SCHEMA_REFRESH_SECONDS)
