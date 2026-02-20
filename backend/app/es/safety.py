from datetime import datetime, timedelta, UTC
from .schema_cache import pattern_allowed
from ..config import settings


class SafetyError(Exception):
    pass


def _flatten(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _flatten(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _flatten(x)


def enforce_pattern(pattern: str):
    if not pattern_allowed(pattern):
        raise SafetyError(f'Pattern not allowlisted: {pattern}')


def enforce_safe_mode(query: dict, size: int | None = None):
    banned_types = {x.strip() for x in settings.SAFE_MODE_DISALLOW_QUERY_TYPES.split(',') if x.strip()}
    banned_fields = {x.strip() for x in settings.SAFE_MODE_DISALLOW_FIELDS.split(',') if x.strip()}
    tokens = set(_flatten(query))
    if tokens & banned_types:
        raise SafetyError(f'Disallowed query types: {sorted(tokens & banned_types)}')
    if tokens & banned_fields:
        raise SafetyError(f'Disallowed fields: {sorted(tokens & banned_fields)}')
    if size and size > settings.SAFE_MODE_MAX_DOCS:
        return settings.SAFE_MODE_MAX_DOCS
    return size


def clamp_time_range(time_range: dict | None) -> dict:
    now = datetime.now(UTC)
    max_from = now - timedelta(days=settings.SAFE_MODE_MAX_TIME_RANGE_DAYS)
    if not time_range:
        return {'from': max_from.isoformat(), 'to': now.isoformat()}
    requested_from = datetime.fromisoformat(time_range['from'].replace('Z', '+00:00'))
    to = datetime.fromisoformat(time_range['to'].replace('Z', '+00:00'))
    if requested_from < max_from:
        requested_from = max_from
    return {'from': requested_from.isoformat(), 'to': to.isoformat()}
