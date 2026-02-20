from datetime import datetime, UTC

AUDIT_LOG: list[dict] = []


def record(event_type: str, payload: dict):
    AUDIT_LOG.append({'ts': datetime.now(UTC).isoformat(), 'event_type': event_type, 'payload': payload})


def list_events() -> list[dict]:
    return AUDIT_LOG[-1000:]
