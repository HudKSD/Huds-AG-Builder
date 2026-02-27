from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Constraints:
    default_time_range_hours: int
    max_time_range_hours: int
    default_max_rows: int
    max_rows: int
    max_terms: int
    request_timeout_seconds: int
    time_field_name: str = "@timestamp"


class ValidationResult(dict):
    pass


def _has_time_filter_dsl(query: dict[str, Any], time_field: str) -> bool:
    q = query.get("query", {})
    stack = [q]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "range" in node and time_field in node["range"]:
                return True
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)
    return False


def validate_query(mode: str, query: str | dict[str, Any], schema_snapshot: dict[str, Any], constraints: Constraints) -> ValidationResult:
    reasons: list[str] = []
    fixes: list[dict[str, Any]] = []
    rewritten = query

    fields = {f["name"] for f in schema_snapshot.get("fields", [])}
    requested_hours = schema_snapshot.get("time_range_hours")
    if requested_hours is None:
        reasons.append("Time range is required.")
        fixes.append({"action": "set_time_range", "value": constraints.default_time_range_hours})
    elif requested_hours > constraints.max_time_range_hours:
        reasons.append("Time range exceeds maximum allowed.")
        fixes.append({"action": "set_time_range", "value": constraints.max_time_range_hours})

    if mode == "esql" and isinstance(query, str):
        upper = query.upper()
        if "MATCH_ALL" in upper and constraints.time_field_name not in query:
            reasons.append("match_all requires time filter.")
        if "*" in query and "LIKE" in upper and "'%" in query:
            reasons.append("Leading wildcard is not allowed.")
        if "LIMIT" not in upper:
            rewritten = query.rstrip(" ;") + f" | LIMIT {constraints.default_max_rows}"
            fixes.append({"action": "add_limit", "value": constraints.default_max_rows})

    if mode == "dsl" and isinstance(query, dict):
        size = int(query.get("size", constraints.default_max_rows))
        if size > constraints.max_rows:
            reasons.append("Requested size exceeds max rows.")
            query["size"] = constraints.max_rows
            fixes.append({"action": "set_size", "value": constraints.max_rows})
        if not _has_time_filter_dsl(query, constraints.time_field_name):
            reasons.append("DSL query must include time range filter.")
            fixes.append({"action": "add_time_filter", "field": constraints.time_field_name})
        unknown_fields = []
        for field in query.get("_source", []):
            if field not in fields:
                unknown_fields.append(field)
        if unknown_fields:
            reasons.append(f"Unknown fields referenced: {', '.join(unknown_fields)}")
            fixes.append({"action": "remove_unknown_fields", "fields": unknown_fields})

    passed = len(reasons) == 0
    return ValidationResult(
        pass_=passed,
        pass_value=passed,
        reasons=reasons,
        suggested_fixes=fixes,
        rewritten_query=rewritten,
    )


def circuit_breaker(preferences: dict[str, Any], constraints: Constraints) -> tuple[bool, list[str], list[dict[str, Any]]]:
    reasons: list[str] = []
    fixes: list[dict[str, Any]] = []
    trh = preferences.get("time_range_hours")
    mr = preferences.get("max_rows")
    if trh is None:
        reasons.append("Missing time_range_hours.")
        fixes.append({"action": "set_time_range", "value": constraints.default_time_range_hours})
    elif int(trh) > constraints.max_time_range_hours:
        reasons.append("time_range_hours exceeds cap.")
        fixes.append({"action": "set_time_range", "value": constraints.max_time_range_hours})
    if mr is None:
        reasons.append("Missing max_rows.")
        fixes.append({"action": "set_max_rows", "value": constraints.default_max_rows})
    elif int(mr) > constraints.max_rows:
        reasons.append("max_rows exceeds cap.")
        fixes.append({"action": "set_max_rows", "value": constraints.max_rows})
    return (len(reasons) > 0, reasons, fixes)
