from ...es.schema_cache import get_schema


async def run(args: dict):
    return await get_schema(args['pattern'])
