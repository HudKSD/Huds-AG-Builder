# Flask + LangGraph Elasticsearch Chat

Production-ready, self-hosted Flask + Jinja2 + vanilla JS app for natural-language Elasticsearch querying with live process viewer and trace replay.

## Run with Docker Compose

```bash
cp .env.example .env
docker-compose up --build
```

Open browser at `http://localhost:8000/`.

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
