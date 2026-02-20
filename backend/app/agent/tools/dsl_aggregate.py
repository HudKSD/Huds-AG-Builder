import time
from ...es.client import get_es_client
from ...es.safety import enforce_pattern, enforce_safe_mode, clamp_time_range
from ...es.schema_cache import get_schema
from ...metrics import es_query_latency_seconds
from .store_result import run as store_result


async def run(args: dict):
    enforce_pattern(args['pattern'])
    enforce_safe_mode(args['aggs_dsl'])
    size = min(args.get('size', 0), 50)
    t0 = time.time()
    es = get_es_client()
    body = {'size': size, 'aggs': args['aggs_dsl']}
    schema = await get_schema(args['pattern'])
    tr = clamp_time_range(args.get('time_range')) if schema.get('time_field') else None
    if tr and schema.get('time_field'):
        body['query'] = {'range': {schema['time_field']: tr}}
    resp = await es.search(index=args['pattern'], body=body)
    await es.close()
    es_query_latency_seconds.labels(tool='dsl_aggregate').observe(time.time() - t0)
    payload = {'aggregations': resp.get('aggregations', {})}
    handle = (await store_result({'payload': payload}))['handle']
    return {'took_ms': resp.get('took', 0), 'aggregations': payload['aggregations'], 'result_handle': handle}
