from quart import Blueprint, jsonify

bp_health = Blueprint('health', __name__)


@bp_health.get('/health')
async def health():
    return jsonify({'status': 'ok'})
