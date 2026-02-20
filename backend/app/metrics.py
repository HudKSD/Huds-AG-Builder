from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

http_requests_total = Counter('http_requests_total', 'Total HTTP requests', ['path', 'method', 'status'])
llm_tokens_in_total = Counter('llm_tokens_in_total', 'LLM input tokens')
llm_tokens_out_total = Counter('llm_tokens_out_total', 'LLM output tokens')
tool_calls_total = Counter('tool_calls_total', 'Tool calls', ['tool'])
tool_errors_total = Counter('tool_errors_total', 'Tool errors', ['tool'])
invalid_tool_calls_total = Counter('invalid_tool_calls_total', 'Invalid tool calls')
es_query_latency_seconds = Histogram('es_query_latency_seconds', 'Elasticsearch query latency', ['tool'])
schema_refresh_duration_seconds = Histogram('schema_refresh_duration_seconds', 'Schema refresh duration')


def metrics_response():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}
