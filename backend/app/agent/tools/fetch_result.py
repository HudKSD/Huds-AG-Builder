from .store_result import STORE


async def run(args: dict):
    return {'payload': STORE.get(args['handle'], {})}
