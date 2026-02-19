import logging
from typing import Any, Dict

from quart import Quart, jsonify, render_template, request
from dotenv import load_dotenv
from elasticsearch import AsyncElasticsearch

from .config import Settings
from .llm_client import OpenAICompatibleClient
from .conversation_store import ConversationStore
from .schema_cache import SchemaCache
from .agent import NL2ESAgent
from .tools.base import ToolContext
from .tools.registry import ToolRegistry
from .tools import ListIndicesTool, GetMappingsTool, EsqlQueryTool, DslSearchTool, GetDocTool, ResolveIndexTool, CountDocsTool

load_dotenv()

app = Quart(__name__)
settings = Settings()

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("nl-es-mvp")


def _es_body(resp: Any) -> Dict[str, Any]:
    """
    elasticsearch-py 8.x returns ObjectApiResponse for many APIs.
    Convert to a JSON-serializable dict.
    """
    if resp is None:
        return {}
    if isinstance(resp, dict):
        return resp
    body = getattr(resp, "body", None)
    if isinstance(body, dict):
        return body
    # last-resort
    try:
        return dict(resp)  # type: ignore[arg-type]
    except Exception:
        return {"value": str(resp)}


@app.before_serving
async def startup():
    # Elasticsearch client (Basic Auth + TLS verify)
    es_kwargs = {
        "basic_auth": (settings.es_username, settings.es_password),
        "verify_certs": settings.es_verify_certs,
        "request_timeout": settings.es_request_timeout,
    }
    if settings.es_ca_certs:
        es_kwargs["ca_certs"] = settings.es_ca_certs
    if settings.es_ssl_assert_fingerprint:
        es_kwargs["ssl_assert_fingerprint"] = settings.es_ssl_assert_fingerprint

    es = AsyncElasticsearch(settings.es_url, **es_kwargs)

    # Connectivity test
    try:
        info_resp = await es.info()
        info = _es_body(info_resp)
        log.info("Connected to Elasticsearch: %s", info.get("cluster_name"))
    except Exception as e:
        log.error("Elasticsearch connection failed: %s", e)

    llm = OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )

    store = ConversationStore(max_turns=settings.conversation_max_turns)
    schema_cache = SchemaCache(es, settings, ttl_seconds=300)

    ctx = ToolContext(es=es, settings=settings, schema_cache=schema_cache)
    registry = ToolRegistry(ctx)

    # Register tools
    registry.register(ListIndicesTool())
    registry.register(ResolveIndexTool())
    registry.register(GetMappingsTool())
    registry.register(CountDocsTool())
    registry.register(EsqlQueryTool())
    registry.register(DslSearchTool())
    registry.register(GetDocTool())

    agent = NL2ESAgent(llm=llm, tools=registry, store=store, max_steps=settings.max_agent_steps)

    app.config["es"] = es
    app.config["llm"] = llm
    app.config["store"] = store
    app.config["schema_cache"] = schema_cache
    app.config["tools"] = registry
    app.config["agent"] = agent


@app.after_serving
async def shutdown():
    es: AsyncElasticsearch = app.config.get("es")
    llm: OpenAICompatibleClient = app.config.get("llm")
    if es:
        await es.close()
    if llm:
        await llm.close()


@app.get("/")
async def index():
    return await render_template("index.html", allowed_patterns=settings.es_allowed_patterns)


@app.get("/api/health")
async def health():
    es: AsyncElasticsearch = app.config.get("es")
    ok = True
    es_info: Dict[str, Any] = {}

    try:
        info_resp = await es.info()
        info = _es_body(info_resp)

        # Return a small, stable payload (better for UI + avoids surprises)
        ver = info.get("version") or {}
        es_info = {
            "cluster_name": info.get("cluster_name"),
            "cluster_uuid": info.get("cluster_uuid"),
            "version": ver.get("number"),
            "build_flavor": ver.get("build_flavor"),
            "tagline": info.get("tagline"),
        }
    except Exception as e:
        ok = False
        es_info = {"error": str(e)}

    return jsonify({"ok": ok, "es": es_info})






@app.post("/api/chat")
async def chat():
    payload = await request.get_json(force=True)
    user_msg = (payload or {}).get("message", "").strip()
    conversation_id = (payload or {}).get("conversation_id")

    store: ConversationStore = app.config["store"]
    agent: NL2ESAgent = app.config["agent"]

    if not conversation_id:
        conversation_id = store.new_id()

    if not user_msg:
        return jsonify({"error": "Empty message", "conversation_id": conversation_id}), 400

    try:
        answer, artifact, trace = await agent.run(conversation_id, user_msg)
        return jsonify({
            "conversation_id": conversation_id,
            "answer": answer,
            "artifact": artifact,
            "trace": trace,
        })
    except Exception as e:
        log.exception("Exception in /api/chat")
        return jsonify({
            "conversation_id": conversation_id,
            "error": str(e),
        }), 500




