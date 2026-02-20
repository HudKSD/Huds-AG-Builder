from .tools import list_indices, get_schema, dsl_search, dsl_aggregate, esql_query, explain_query, store_result, fetch_result

TOOLS = {
    'list_indices': list_indices.run,
    'get_schema': get_schema.run,
    'dsl_search': dsl_search.run,
    'dsl_aggregate': dsl_aggregate.run,
    'esql_query': esql_query.run,
    'explain_query': explain_query.run,
    'store_result': store_result.run,
    'fetch_result': fetch_result.run,
}
