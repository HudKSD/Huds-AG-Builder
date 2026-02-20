import secrets

STORE: dict[str, dict] = {}


async def run(args: dict):
    handle = secrets.token_hex(8)
    STORE[handle] = args['payload']
    return {'handle': handle}
