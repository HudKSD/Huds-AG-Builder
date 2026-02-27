from validators import Constraints, circuit_breaker, validate_query


def constraints():
    return Constraints(
        default_time_range_hours=24,
        max_time_range_hours=168,
        default_max_rows=200,
        max_rows=200,
        max_terms=20,
        request_timeout_seconds=20,
    )


def test_validate_query_missing_time_range():
    schema = {"fields": [{"name": "@timestamp", "type": "date"}]}
    report = validate_query("dsl", {"query": {"match": {"message": "x"}}, "size": 10}, schema, constraints())
    assert not report["pass_value"]
    assert any("Time range" in r for r in report["reasons"])


def test_validate_query_time_range_too_wide():
    schema = {"fields": [{"name": "@timestamp", "type": "date"}], "time_range_hours": 500}
    report = validate_query("esql", "FROM logs-* | LIMIT 10", schema, constraints())
    assert not report["pass_value"]
    assert any("exceeds" in r for r in report["reasons"])


def test_validate_query_limit_too_high_and_unknown_field():
    schema = {"fields": [{"name": "@timestamp", "type": "date"}, {"name": "message", "type": "text"}], "time_range_hours": 24}
    q = {
        "size": 1000,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-1h", "lte": "now"}}}]}},
        "_source": ["message", "unknown.field"],
    }
    report = validate_query("dsl", q, schema, constraints())
    assert not report["pass_value"]
    assert any("size" in r for r in report["reasons"])
    assert any("Unknown fields" in r for r in report["reasons"])


def test_circuit_breaker_blocked_with_fixes():
    blocked, reasons, fixes = circuit_breaker({"time_range_hours": 1000, "max_rows": 1000}, constraints())
    assert blocked
    assert reasons
    assert fixes
