import json
import uuid
from quart import Blueprint, request, jsonify, Response
from ..rbac import require_permission
from ..agent.orchestrator import run_round
from ..audit import record
from ..agent.tools.fetch_result import run as fetch_result
from ..config import allowlist_patterns

bp_chat = Blueprint('chat', __name__)
CONVERSATIONS = {}


@bp_chat.post('/api/chat')
@require_permission('chat:write')
async def chat():
    data = await request.get_json()
    msg = data.get('message', '')
    patterns = data.get('patterns') or allowlist_patterns()
    result = await run_round(msg, patterns)
    cid = str(uuid.uuid4())
    CONVERSATIONS[cid] = {'id': cid, 'message': msg, 'result': result}
    record('chat', {'conversation_id': cid, 'patterns': patterns})
    return jsonify({'conversation_id': cid, **result})


@bp_chat.post('/api/chat/stream')
@require_permission('chat:write')
async def chat_stream():
    data = await request.get_json()
    msg = data.get('message', '')
    patterns = data.get('patterns') or allowlist_patterns()
    result = await run_round(msg, patterns)

    async def gen():
        for step in result['steps']:
            yield f"data: {json.dumps(step)}\n\n"
        yield f"data: {json.dumps({'type': 'message_complete', 'text': result['answer']})}\n\n"

    return Response(gen(), content_type='text/event-stream')


@bp_chat.get('/api/conversations/<cid>')
@require_permission('conversation:read')
async def get_conversation(cid: str):
    return jsonify(CONVERSATIONS.get(cid, {'error': 'not_found'}))


@bp_chat.get('/api/result/<handle>')
@require_permission('conversation:read')
async def get_result(handle: str):
    return jsonify(await fetch_result({'handle': handle}))
