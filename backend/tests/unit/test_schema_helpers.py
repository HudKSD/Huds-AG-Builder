from app.es.schema_cache import _detect_time_field, _detect_keyword_variant


def test_time_field_detection():
    fields = {'foo': {'type': 'keyword'}, '@timestamp': {'type': 'date'}}
    assert _detect_time_field(fields) == '@timestamp'


def test_keyword_variant_detection():
    fields = {'event.action': {'type': 'text'}, 'event.action.keyword': {'type': 'keyword'}}
    assert _detect_keyword_variant(fields, 'event.action') == 'event.action.keyword'
