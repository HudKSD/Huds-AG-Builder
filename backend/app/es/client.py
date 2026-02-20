from elasticsearch import AsyncElasticsearch
from ..config import settings


def get_es_client() -> AsyncElasticsearch:
    opts = {
        'hosts': [settings.ES_URL],
        'basic_auth': (settings.ES_USERNAME, settings.ES_PASSWORD),
        'verify_certs': settings.ES_VERIFY_SSL,
        'request_timeout': settings.ES_REQUEST_TIMEOUT,
    }
    if settings.ES_CA_CERT_PATH:
        opts['ca_certs'] = settings.ES_CA_CERT_PATH
    return AsyncElasticsearch(**opts)
