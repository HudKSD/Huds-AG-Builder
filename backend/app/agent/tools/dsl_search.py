import time
from ...es.client import get_es_client
from ...es.safety import enforce_pattern, enforce_safe_mode, clamp_time_range
from ...es.schema_cache import get_schema
from ...metrics import es_query_latency_seconds
from .store_result import run as store_result


async def run(args: dict):
    enforce_pattern(args['pattern'])
    size = enforce_safe_mode(args['query_dsl'], args.get('size', 10))
    t0 = time.time()
    es = get_es_client()
    body = {'query': args['query_dsl'], 'size': size}
    if args.get('sort'):
        body['sort'] = args['sort']
    schema = await get_schema(args['pattern'])
    tr = clamp_time_range(args.get('time_range')) if schema.get('time_field') else None
    if tr and schema.get('time_field'):
        body['query'] = {'bool': {'must': [args['query_dsl']], 'filter': [{'range': {schema['time_field']: tr}}]}}
    resp = await es.search(index=args['pattern'], body=body, track_total_hits=args.get('track_total_hits', True))
    await es.close()
    es_query_latency_seconds.labels(tool='dsl_search').observe(time.time() - t0)
    payload = {'hits': resp.get('hits', {}).get('hits', [])}
    handle = (await store_result({'payload': payload}))['handle']
    return {
        'took_ms': resp.get('took', 0),
        'total_hits': resp.get('hits', {}).get('total', {}).get('value', 0),
        'hits_sample': payload['hits'][: min(size, 10)],
        'result_handle': handle,
    }
