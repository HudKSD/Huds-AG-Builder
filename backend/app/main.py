import asyncio
from quart import Quart, g, request
from .config import settings
from .auth import auth_middleware
from .rate_limit import rate_limit_middleware
from .logging_setup import setup_logging
from .metrics import http_requests_total
from .otel import setup_otel
from .es.schema_cache import scheduler_loop
from .api.routes_chat import bp_chat
from .api.routes_schema import bp_schema
from .api.routes_admin import bp_admin
from .api.routes_health import bp_health
from .api.routes_metrics import bp_metrics


def create_app() -> Quart:
    setup_logging(settings.LOG_LEVEL)
    app = Quart(__name__)
    setup_otel()

    app.register_blueprint(bp_chat)
    app.register_blueprint(bp_schema)
    app.register_blueprint(bp_admin)
    app.register_blueprint(bp_health)
    app.register_blueprint(bp_metrics)

    @app.before_request
    async def before_req():
        await auth_middleware()
        limited = await rate_limit_middleware()
        if limited:
            return limited

    @app.after_request
    async def after_req(resp):
        http_requests_total.labels(path=request.path, method=request.method, status=str(resp.status_code)).inc()
        return resp

    @app.before_serving
    async def startup():
        app.schema_task = asyncio.create_task(scheduler_loop())

    @app.after_serving
    async def shutdown():
        app.schema_task.cancel()

    return app


app = create_app()
