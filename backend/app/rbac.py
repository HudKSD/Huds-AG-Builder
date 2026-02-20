from functools import wraps
from quart import g, jsonify

ROLE_PERMISSIONS = {
    'viewer': {'schema:read', 'indices:read', 'conversation:read'},
    'analyst': {
        'schema:read', 'indices:read', 'conversation:read', 'chat:write',
        'tool:read', 'trace:read'
    },
    'admin': {'*'},
}


def has_permission(role: str, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return '*' in perms or permission in perms


def require_permission(permission: str):
    def decorator(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            role = getattr(g, 'role', 'viewer')
            if not has_permission(role, permission):
                return jsonify({'error': 'forbidden', 'required': permission}), 403
            return await fn(*args, **kwargs)

        return wrapped

    return decorator
