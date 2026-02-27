# Flask + LangGraph Elasticsearch Chat

Production-ready, self-hosted Flask + Jinja2 + vanilla JS app for natural-language Elasticsearch querying with live process viewer and trace replay.

## Run with Docker Compose

### Option A (from app folder)
```bash
cd flask_langgraph_app
cp .env.example .env
docker-compose up --build
```

### Option B (from repository root)
```bash
cp flask_langgraph_app/.env.example flask_langgraph_app/.env
docker-compose -f flask_langgraph_app/docker-compose.yml --env-file flask_langgraph_app/.env up --build
```

Open browser at `http://localhost:8000/`.

Expected containers/services for this app:
- `flask-langgraph-es-chat-app` only.
- No `postgres` / `redis` containers (this app uses SQLite).

Expected app server:
- Gunicorn + gevent (not hypercorn).
- Container logs should include: `[flask-langgraph-es-chat] Starting Gunicorn with gevent worker`.

## Troubleshooting (important)

If logs show `postgres-1`, `redis-1`, or `hypercorn app.main`, that is a different compose stack.
Run this exact reset:

```bash
# from repo root
docker-compose down --remove-orphans || true
docker-compose -f flask_langgraph_app/docker-compose.yml --env-file flask_langgraph_app/.env down --remove-orphans || true
docker-compose -f flask_langgraph_app/docker-compose.yml --env-file flask_langgraph_app/.env up --build
```

Runtime verification:

```bash
curl -s http://localhost:8000/health
# expected keys: service=flask-langgraph-es-chat, storage=sqlite, server_expected=gunicorn+gevent

curl -s http://localhost:8000/metrics | head
docker-compose logs app | grep -E "Gunicorn|hypercorn"
```

## Streaming behavior

`/chat/stream` and `/dry_run/stream` return NDJSON events in this shape:

```json
{"type":"node_start","node":"planner","ts":...}
{"type":"tool_call","tool":"get_field_caps","args":{"index":"logs-*"}}
{"type":"node_end","node":"schema_fetch","duration_ms":123}
{"type":"blocked","reason":"...","suggested_fixes":[...]}
{"type":"final","answer":"...","trace_id":"...","debug":{...}}
```

UI uses `fetch()` streaming and updates timeline, tool calls, block banners, query diff, and evidence cards in real time.

## API examples

### /chat
```bash
curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"show failed login trends","preferences":{"mode":"auto","time_range_hours":24,"max_rows":50}}'
```

### /chat/stream
```bash
curl -N http://localhost:8000/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"top source ips for failed logins"}'
```

### /dry_run
```bash
curl -s http://localhost:8000/dry_run \
  -H 'Content-Type: application/json' \
  -d '{"message":"failed logins in auth logs"}'
```

### /replay/stream
```bash
curl -N http://localhost:8000/replay/stream \
  -H 'Content-Type: application/json' \
  -d '{"trace_id":"<trace-id>","from_node":"query_generate"}'
```
