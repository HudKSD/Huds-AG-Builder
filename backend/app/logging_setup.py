import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'level': record.levelname, 'message': record.getMessage(), 'logger': record.name})


def setup_logging(level: str = 'INFO'):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
