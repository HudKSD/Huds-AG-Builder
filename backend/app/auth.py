import jwt
from quart import g, request
from .config import settings


def parse_bearer() -> tuple[str, str]:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token = auth.replace('Bearer ', '', 1)
        try:
            payload = jwt.decode(token, settings.SECURITY_JWT_SECRET, algorithms=['HS256'])
            return payload.get('sub', 'anonymous'), payload.get('role', settings.SECURITY_RBAC_DEFAULT_ROLE)
        except Exception:
            return 'anonymous', 'viewer'
    return 'anonymous', settings.SECURITY_RBAC_DEFAULT_ROLE


async def auth_middleware():
    if not settings.SECURITY_ENABLE_AUTH:
        g.user_id = 'dev'
        g.role = 'admin'
        return
    user_id, role = parse_bearer()
    g.user_id = user_id
    g.role = role
