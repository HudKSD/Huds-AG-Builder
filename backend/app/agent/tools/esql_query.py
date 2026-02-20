from ...es.esql import execute_esql
from .store_result import run as store_result


async def run(args: dict):
    result = await execute_esql(args['query'])
    if not result.get('ok'):
        return result
    handle = (await store_result({'payload': result}))['handle']
    return {
        'took_ms': result['took_ms'],
        'columns': result['columns'],
        'rows': result['rows'][:200],
        'row_count': result['row_count'],
        'result_handle': handle,
    }
