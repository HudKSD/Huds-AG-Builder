# Elastic Agent Builder (Production Scaffold)

## Run
1. `cp .env.example .env`
2. `docker compose up --build`
3. Open `http://localhost:8800/health` and `http://localhost:8800/metrics`

## Architecture
- **Backend**: Quart + Hypercorn with RBAC, rate limiting, audit log, strict tool loop, ES schema cache, safety enforcement, metrics and OTEL hook.
- **Agent**: deterministic orchestration loop with step traces (`reasoning`, `tool_call`, `tool_result`, `message_complete`, `round_complete`), tool registry, verifier.
- **Elasticsearch layer**: schema-first discovery via mappings and field_caps, allowlist enforcement, safe-mode clamps.
- **Frontend**: Vite + React + Tailwind SOC-console style with chat panel, trace timeline, query inspector, schema/admin placeholders.

## Threat model summary
- Prompt injection treated as untrusted data by system policy and context pack.
- Unsafe tools prevented by allowlisted patterns and safe-mode query checks.
- Least-privilege with role gates: viewer / analyst / admin.
- Secret redaction utility for logs and payload handling.
- Audit events recorded for chat operations.

## Acceptance tests
- Unit tests for: time field detection, keyword variant detection, safety rejects forbidden query types, token-budget trimming, verifier mismatch.
- Integration tests run only when `ES_URL` is reachable.

## Notes
- ES|QL support is runtime-detected (`esql_query` returns structured unsupported error when not available).
- Optional reverse proxy TLS termination (Caddy/Nginx) can sit in front of `app` service.
