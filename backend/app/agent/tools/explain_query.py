from ...es.schema_cache import get_schema
from ...es.safety import enforce_safe_mode


async def run(args: dict):
    schema = await get_schema(args['pattern'])
    fields = {f['name'] for f in schema['fields']}
    text = str(args['dsl_or_esql'])
    detected = [f for f in fields if f in text]
    unknown = [tok for tok in text.replace('"', ' ').replace("'", ' ').split() if '.' in tok and tok not in fields][:20]
    issues = []
    try:
        if isinstance(args['dsl_or_esql'], dict):
            enforce_safe_mode(args['dsl_or_esql'])
    except Exception as exc:
        issues.append(str(exc))
    return {
        'detected_fields': detected,
        'unknown_fields': unknown,
        'suggested_fields': [f['keyword_variant'] for f in schema['fields'] if f.get('keyword_variant')][:10],
        'explanation': 'Validated fields against discovered schema.',
        'safe_mode_issues': issues,
    }
