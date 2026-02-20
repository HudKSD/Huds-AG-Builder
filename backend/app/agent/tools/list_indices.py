from ...es.client import get_es_client


async def run(args: dict):
    es = get_es_client()
    patterns = args.get('patterns', [])
    indices = []
    resolved = []
    for p in patterns:
        resp = await es.cat.indices(index=p, format='json')
        for row in resp:
            indices.append({'name': row.get('index'), 'type': 'index', 'health': row.get('health')})
            resolved.append(row.get('index'))
    await es.close()
    return {'indices': indices, 'resolved_patterns': sorted(set(resolved))}
