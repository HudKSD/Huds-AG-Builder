from quart import Blueprint, request, jsonify
from ..rbac import require_permission
from ..es.schema_cache import get_schema
from ..agent.tools.list_indices import run as list_indices
from ..config import allowlist_patterns

bp_schema = Blueprint('schema', __name__)


@bp_schema.get('/api/indices')
@require_permission('indices:read')
async def indices():
    result = await list_indices({'patterns': allowlist_patterns()})
    return jsonify(result)


@bp_schema.get('/api/schema')
@require_permission('schema:read')
async def schema():
    pattern = request.args.get('pattern', allowlist_patterns()[0])
    result = await get_schema(pattern)
    return jsonify(result)
