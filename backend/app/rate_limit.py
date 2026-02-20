import time
from collections import defaultdict, deque
from quart import jsonify, g
from .config import settings

WINDOWS: dict[str, deque[float]] = defaultdict(deque)


async def rate_limit_middleware():
    now = time.time()
    window = WINDOWS[g.user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= settings.RATE_LIMIT_RPM:
        return jsonify({'error': 'rate_limited'}), 429
    window.append(now)
    return None
