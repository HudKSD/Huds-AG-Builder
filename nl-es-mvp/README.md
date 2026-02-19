# NL → Elasticsearch MVP (Quart)

## What it does
- Chat UI
- LLM tool-calling loop
- Tools query Elasticsearch (ES|QL / DSL)
- Returns: answer + query used + results table

## Run
1) Copy .env.example → .env and fill:
   - ES_URL, ES_USERNAME, ES_PASSWORD
   - ES_ALLOWED_PATTERNS
   - TLS settings (ES_CA_CERTS or fingerprint)
   - LLM_API_KEY, LLM_MODEL

2) If using a private CA:
   - put your CA at ./certs/ca.crt
   - set ES_CA_CERTS=/certs/ca.crt

3) Start:
   docker compose up --build

4) Open:
   http://localhost:8000

## Extend
- Add more tools in app/tools/es_tools.py
- Register them in app/main.py tool registry
- Adjust agent prompt in app/agent.py

