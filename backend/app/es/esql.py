from .client import get_es_client


async def execute_esql(query: str) -> dict:
    es = get_es_client()
    try:
        resp = await es.esql.query(query=query)
        return {
            'ok': True,
            'took_ms': resp.get('took', 0),
            'columns': resp.get('columns', []),
            'rows': resp.get('values', []),
            'row_count': len(resp.get('values', [])),
        }
    except Exception as exc:
        return {'ok': False, 'error': {'type': 'unsupported_or_failed', 'message': str(exc)}}
    finally:
        await es.close()
