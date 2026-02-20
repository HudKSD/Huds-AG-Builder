from quart import Blueprint
from ..metrics import metrics_response

bp_metrics = Blueprint('metrics', __name__)


@bp_metrics.get('/metrics')
async def metrics():
    return metrics_response()
