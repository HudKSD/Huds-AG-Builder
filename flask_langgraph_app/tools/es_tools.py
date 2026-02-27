from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

from elasticsearch import Elasticsearch
from pydantic import BaseModel, Field

from validators import Constraints, validate_query


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time_range(hours: int, field: str) -> dict[str, Any]:
    return {"range": {field: {"gte": (_now() - timedelta(hours=hours)).isoformat(), "lte": _now().isoformat()}}}


class ListIndicesIn(BaseModel):
    prefix: str | None = None
    wildcard: str | None = None
    limit: int = 50


class ListIndicesOut(BaseModel):
    indices: list[str]


class FieldCapsIn(BaseModel):
    index: str


class FieldCapsOut(BaseModel):
    fields: list[dict[str, Any]]
    time_field: str


class TermsIn(BaseModel):
    index: str
    field: str
    time_range_hours: int
    max_terms: int


class TermsOut(BaseModel):
    terms: list[dict[str, Any]]


class GenerateQueryIn(BaseModel):
    intent: str
    index: str
    time_range_hours: int
    limit: int


class GenerateESQLOut(BaseModel):
    esql: str


class GenerateDSLOut(BaseModel):
    dsl: dict[str, Any]


class ValidateIn(BaseModel):
    mode: str
    query: dict[str, Any] | str
    schema_snapshot: dict[str, Any]


class ValidateOut(BaseModel):
    pass_value: bool
    reasons: list[str]
    suggested_fixes: list[dict[str, Any]]
    rewritten_query: dict[str, Any] | str | None = None


class ExecuteIn(BaseModel):
    query: dict[str, Any] | str
    index: str


class ExecuteOut(BaseModel):
    row_count: int
    sample_rows: list[dict[str, Any]]


class EvidenceIn(BaseModel):
    intent: str
    query: dict[str, Any] | str
    results: dict[str, Any]
    baseline_results: dict[str, Any] | None = None


class EvidenceOut(BaseModel):
    cards: list[dict[str, Any]]


class DeltaIn(BaseModel):
    recent_summary: dict[str, Any]
    baseline_summary: dict[str, Any]


class DeltaOut(BaseModel):
    delta: dict[str, Any]


class ESTools:
    def __init__(self, es: Elasticsearch, constraints: Constraints):
        self.es = es
        self.constraints = constraints

    def list_indices(self, prefix: str | None = None, wildcard: str | None = None, limit: int = 50) -> dict[str, Any]:
        pattern = wildcard or (f"{prefix}*" if prefix else "*")
        indices = self.es.indices.get(index=pattern, expand_wildcards="open")
        names = sorted(indices.keys())[: min(limit, 50)]
        return {"indices": names}

    def get_field_caps(self, index: str) -> dict[str, Any]:
        caps = self.es.field_caps(index=index, fields="*")
        fields = []
        for name, info in caps.get("fields", {}).items():
            type_name = next(iter(info.keys()))
            fields.append({"name": name, "type": type_name})
        return {"fields": fields[:300], "time_field": self.constraints.time_field_name}

    def schema_terms(self, index: str, field: str, time_range_hours: int, max_terms: int) -> dict[str, Any]:
        trh = min(time_range_hours, self.constraints.max_time_range_hours)
        mterms = min(max_terms, self.constraints.max_terms)
        body = {
            "size": 0,
            "query": {"bool": {"filter": [_time_range(trh, self.constraints.time_field_name)]}},
            "aggs": {"top_terms": {"terms": {"field": field, "size": mterms}}},
        }
        resp = self.es.search(index=index, body=body, request_timeout=self.constraints.request_timeout_seconds)
        buckets = resp.get("aggregations", {}).get("top_terms", {}).get("buckets", [])
        return {"terms": [{"key": b["key"], "count": b["doc_count"]} for b in buckets]}

    def generate_esql(self, intent: str, index: str, time_range_hours: int, limit: int) -> dict[str, Any]:
        lim = min(limit, self.constraints.max_rows)
        esql = (
            f"FROM {index} | WHERE {self.constraints.time_field_name} >= NOW() - {time_range_hours} HOURS "
            f"| KEEP {self.constraints.time_field_name}, message, host.name, source.ip | LIMIT {lim}"
        )
        if "failed login" in intent.lower():
            esql += " | WHERE event.outcome == \"failure\""
        return {"esql": esql}

    def generate_dsl(self, intent: str, index: str, time_range_hours: int, size: int) -> dict[str, Any]:
        lim = min(size, self.constraints.max_rows)
        return {
            "dsl": {
                "size": lim,
                "query": {
                    "bool": {
                        "filter": [_time_range(time_range_hours, self.constraints.time_field_name)],
                        "must": [{"match": {"message": intent}}],
                    }
                },
                "_source": [self.constraints.time_field_name, "message", "host.name", "source.ip", "user.name"],
                "sort": [{self.constraints.time_field_name: "desc"}],
            }
        }

    def validate(self, mode: str, query: str | dict[str, Any], schema_snapshot: dict[str, Any]) -> dict[str, Any]:
        report = validate_query(mode, query, schema_snapshot, self.constraints)
        return {
            "pass_value": bool(report["pass_value"]),
            "reasons": report["reasons"],
            "suggested_fixes": report["suggested_fixes"],
            "rewritten_query": report.get("rewritten_query"),
        }

    def execute_esql(self, esql: str, index: str) -> dict[str, Any]:
        enforced = esql if "LIMIT" in esql.upper() else f"{esql} | LIMIT {self.constraints.default_max_rows}"
        resp = self.es.esql.query(query=enforced, request_timeout=self.constraints.request_timeout_seconds)
        rows = []
        columns = [c.get("name") for c in resp.get("columns", [])]
        for row in resp.get("values", [])[: self.constraints.max_rows]:
            rows.append({k: v for k, v in zip(columns, row)})
        return {"row_count": len(rows), "sample_rows": rows[:10]}

    def execute_dsl(self, dsl: dict[str, Any], index: str) -> dict[str, Any]:
        body = copy.deepcopy(dsl)
        body["size"] = min(int(body.get("size", self.constraints.default_max_rows)), self.constraints.max_rows)
        resp = self.es.search(index=index, body=body, request_timeout=self.constraints.request_timeout_seconds)
        hits = resp.get("hits", {}).get("hits", [])
        rows = [h.get("_source", {}) for h in hits[: self.constraints.max_rows]]
        return {"row_count": len(rows), "sample_rows": rows[:10]}

    def build_evidence_cards(self, intent: str, query: dict[str, Any] | str, results: dict[str, Any], baseline_results: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = results.get("sample_rows", [])
        card = {
            "claim": f"Findings for intent: {intent}",
            "evidence": {"query": query, "row_count": results.get("row_count", 0)},
            "query": query,
            "metrics": {"row_count": results.get("row_count", 0)},
            "examples": rows[:3],
            "confidence": 0.7 if results.get("row_count", 0) else 0.3,
            "next_steps": ["Refine with narrower filters", "Validate top entities in Schema Lens"],
        }
        if baseline_results:
            card["metrics"]["baseline_row_count"] = baseline_results.get("row_count", 0)
        return {"cards": [card]}

    def compute_baseline_delta(self, recent_summary: dict[str, Any], baseline_summary: dict[str, Any]) -> dict[str, Any]:
        recent = recent_summary.get("row_count", 0)
        base = baseline_summary.get("row_count", 0)
        abs_delta = recent - base
        pct = ((abs_delta / base) * 100) if base else None
        return {"delta": {"abs": abs_delta, "pct": pct}}

    def playbook_failed_logins(self, index: str, hours: int, limit: int) -> dict[str, Any]:
        dsl = {
            "size": min(limit, self.constraints.max_rows),
            "query": {
                "bool": {
                    "filter": [
                        _time_range(hours, self.constraints.time_field_name),
                        {"term": {"event.outcome": "failure"}},
                    ]
                }
            },
        }
        return self.execute_dsl(dsl, index)

    def playbook_top_source_ips_failed_logins(self, index: str, hours: int, max_terms: int) -> dict[str, Any]:
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        _time_range(hours, self.constraints.time_field_name),
                        {"term": {"event.outcome": "failure"}},
                    ]
                }
            },
            "aggs": {"ips": {"terms": {"field": "source.ip", "size": min(max_terms, self.constraints.max_terms)}}},
        }
        resp = self.es.search(index=index, body=body, request_timeout=self.constraints.request_timeout_seconds)
        return {"terms": resp.get("aggregations", {}).get("ips", {}).get("buckets", [])}
