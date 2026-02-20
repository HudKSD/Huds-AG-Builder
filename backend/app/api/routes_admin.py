from quart import Blueprint, jsonify
from ..rbac import require_permission
from ..audit import list_events

bp_admin = Blueprint('admin', __name__)


@bp_admin.get('/api/audit')
@require_permission('audit:read')
async def audit():
    return jsonify({'events': list_events()})
