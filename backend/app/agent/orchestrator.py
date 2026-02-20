from pydantic import BaseModel, Field
from .context import build_briefing_pack
from .tool_runner import run_tool
from .verifier import verify_answer
from ..config import settings
from ..es.schema_cache import get_schema


class NextAction(BaseModel):
    action: str = Field(pattern='^(tool_call|final)$')
    reasoning: str
    tool: str | None = None
    args: dict | None = None


async def simple_planner(message: str, patterns: list[str]) -> NextAction:
    m = message.lower()
    if 'schema' in m:
        return NextAction(action='tool_call', reasoning='Need schema facts', tool='get_schema', args={'pattern': patterns[0]})
    if 'esql' in m:
        return NextAction(action='tool_call', reasoning='Need esql facts', tool='esql_query', args={'query': message})
    return NextAction(action='tool_call', reasoning='Need search facts', tool='dsl_search', args={'pattern': patterns[0], 'query_dsl': {'match_all': {}}, 'size': 5})


async def run_round(user_message: str, patterns: list[str]):
    steps = []
    tool_results = []
    schemas = [await get_schema(patterns[0])] if patterns else []
    _ = build_briefing_pack(user_message, patterns, schemas, memory='N/A', digests='none')

    explained = False
    for _step in range(settings.AGENT_MAX_TOOL_STEPS):
        action = await simple_planner(user_message, patterns)
        steps.append({'type': 'reasoning', 'text': action.reasoning})
        if action.action == 'final':
            break

        if action.tool in {'dsl_search', 'dsl_aggregate'} and not explained:
            exp = await run_tool('explain_query', {'pattern': patterns[0], 'dsl_or_esql': action.args.get('query_dsl', {})})
            steps.append({'type': 'tool_result', 'tool': 'explain_query', 'ok': exp['ok'], 'result': exp.get('result'), 'error': exp.get('error')})
            explained = True

        steps.append({'type': 'tool_call', 'tool': action.tool, 'args': action.args})
        result = await run_tool(action.tool, action.args or {})
        steps.append({'type': 'tool_result', 'tool': action.tool, 'ok': result['ok'], 'result': result.get('result'), 'error': result.get('error')})
        tool_results.append(result)
        if result['ok']:
            final = f"Tool {action.tool} executed successfully. Evidence: {result['result']}"
            ok, why = verify_answer(final, tool_results)
            if not ok:
                final = f'Unable to verify claim: {why}'
            steps.append({'type': 'message_complete', 'text': final})
            steps.append({'type': 'round_complete', 'round_id': 'r1'})
            return {'answer': final, 'steps': steps}

    final = 'Reached step limit with best effort.'
    steps.append({'type': 'message_complete', 'text': final})
    steps.append({'type': 'round_complete', 'round_id': 'r1'})
    return {'answer': final, 'steps': steps}
