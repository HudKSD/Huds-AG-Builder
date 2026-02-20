from ..metrics import tool_calls_total, tool_errors_total
from .tool_registry import TOOLS


async def run_tool(name: str, args: dict):
    tool_calls_total.labels(tool=name).inc()
    fn = TOOLS.get(name)
    if not fn:
        tool_errors_total.labels(tool=name).inc()
        return {'ok': False, 'error': 'unknown_tool'}
    try:
        result = await fn(args)
        return {'ok': True, 'result': result}
    except Exception as exc:
        tool_errors_total.labels(tool=name).inc()
        return {'ok': False, 'error': str(exc)}
