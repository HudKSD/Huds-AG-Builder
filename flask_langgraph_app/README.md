# Flask + LangGraph Elasticsearch Chat

Production-ready, self-hosted Flask + Jinja2 + vanilla JS app for natural-language Elasticsearch querying with live process viewer and trace replay.

## Run with Docker Compose

```bash
cd flask_langgraph_app
cp .env.example .env
docker-compose up --build
```

Open browser at `http://localhost:8000/`.

Expected service list:
- `app` only (this project uses SQLite by design).

Expected app log line:
- Gunicorn startup, **not** Hypercorn.

## Troubleshooting (important)

If you see services like `postgres`/`redis` or logs mentioning `hypercorn app.main`, you are running a different compose file from the repository root. Run compose from `flask_langgraph_app/` instead:

```bash
cd flask_langgraph_app
docker-compose down --remove-orphans
docker-compose up --build
```

Quick checks:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/metrics | head
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
