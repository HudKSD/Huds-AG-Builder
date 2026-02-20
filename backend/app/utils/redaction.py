SENSITIVE_KEYS = {'authorization', 'api_key', 'password', 'token'}


def redact_dict(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_KEYS:
            out[k] = '[REDACTED]'
        elif isinstance(v, dict):
            out[k] = redact_dict(v)
        else:
            out[k] = v
    return out
