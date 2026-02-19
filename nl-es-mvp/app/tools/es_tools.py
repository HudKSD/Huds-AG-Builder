import fnmatch
import re
from typing import Any, Dict, List, Optional

from .base import Tool, ToolContext


# -----------------------------
# Allowlist / parsing helpers
# -----------------------------
def _is_allowed_source(source: str, allowed_patterns: List[str]) -> bool:
    source = source.strip().strip('"').strip("'")
    has_wildcards = ("*" in source) or ("?" in source)
    if has_wildcards:
        # safest: only allow exact allowlisted patterns when wildcards are present
        return source in allowed_patterns
    # concrete index name
    return any(fnmatch.fnmatch(source, pat) for pat in allowed_patterns)


def _extract_esql_from_source(query: str) -> Optional[str]:
    # Capture token after FROM (up to first whitespace or pipe)
    m = re.search(r"(?i)\bFROM\s+([^\s|]+)", query)
    if not m:
        return None
    return m.group(1)


def _enforce_limit(esql: str, hard_cap: int, requested: Optional[int]) -> str:
    cap = min(requested or hard_cap, hard_cap)

    # If query already has LIMIT, reduce if needed.
    m = re.search(r"(?i)\|\s*LIMIT\s+(\d+)\b", esql)
    if m:
        n = int(m.group(1))
        if n > cap:
            esql = re.sub(r"(?i)(\|\s*LIMIT\s+)\d+\b", rf"\g<1>{cap}", esql)
        return esql

    # Add LIMIT at end
    return esql.rstrip() + f"\n| LIMIT {cap}"


def _build_filter(
    time_field: Optional[str],
    time_from: Optional[str],
    time_to: Optional[str],
    extra: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []

    if time_field and (time_from or time_to):
        r: Dict[str, Any] = {}
        if time_from:
            r["gte"] = time_from
        if time_to:
            r["lte"] = time_to
        parts.append({"range": {time_field: r}})

    if extra:
        parts.append(extra)

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"bool": {"filter": parts}}


# -----------------------------
# Field caps + time probing
# -----------------------------
async def _field_caps_for_fields(ctx: ToolContext, index: str, fields: List[str]) -> Dict[str, Any]:
    """
    Return compact field_caps summary for a subset of fields.
    Works even if the client lacks a direct method (falls back to transport request).
    """
    fields = [f for f in fields if f]
    if not fields:
        return {}

    fields_param = ",".join(fields[:500])

    try:
        resp = await ctx.es.field_caps(index=index, fields=fields_param)
    except Exception:
        # Fallback to raw request
        try:
            path = f"/{index}/_field_caps"
            resp = await ctx.es.transport.perform_request("GET", path, params={"fields": fields_param})
        except Exception:
            return {}

    out: Dict[str, Any] = {}
    fields_obj = (resp or {}).get("fields") or {}
    for field, type_map in fields_obj.items():
        searchable = False
        aggregatable = False
        types = []
        for t, meta in (type_map or {}).items():
            types.append(t)
            if isinstance(meta, dict):
                searchable = searchable or bool(meta.get("searchable"))
                aggregatable = aggregatable or bool(meta.get("aggregatable"))
        out[field] = {"types": types, "searchable": searchable, "aggregatable": aggregatable}
    return out


async def _pick_time_field_by_probe(
    ctx: ToolContext,
    index: str,
    candidates: List[str],
    gte: str,
    lte: str
) -> Optional[str]:
    """
    Pick a date field that actually returns hits in the time window.
    Works even when @timestamp exists in some indices but not others.
    """
    for f in candidates[:12]:
        try:
            r = await ctx.es.count(index=index, query={"range": {f: {"gte": gte, "lte": lte}}})
            if (r or {}).get("count", 0) > 0:
                return f
        except Exception:
            continue
    return candidates[0] if candidates else None


# -----------------------------
# Tool: list_indices
# -----------------------------
class ListIndicesTool(Tool):
    name = "list_indices"
    description = "List indices/data streams matching an allowed pattern (for discovery)."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Index pattern to list. Must match allowlist."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
        },
        "required": ["pattern"]
    }

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        pattern = args["pattern"].strip()
        if not _is_allowed_source(pattern, ctx.settings.es_allowed_patterns):
            return {"error": f"Index/pattern not allowed. Allowed patterns: {ctx.settings.es_allowed_patterns}"}

        max_results = int(args.get("max_results", 50))
        max_results = min(max_results, 500)

        try:
            rows = await ctx.es.cat.indices(index=pattern, format="json")
            names = [r.get("index") for r in rows if r.get("index")]
            return {"pattern": pattern, "count": len(names), "indices": names[:max_results]}
        except Exception:
            resp = await ctx.es.indices.get(index=pattern, allow_no_indices=True)
            names = list((resp or {}).keys())
            return {"pattern": pattern, "count": len(names), "indices": names[:max_results]}


# -----------------------------
# Tool: get_mappings (schema-first, important fields + field_caps + probe time field)
# -----------------------------
class GetMappingsTool(Tool):
    name = "get_mappings"
    description = "Get prioritized mapping fields (field -> type), field_caps flags, and best date field for an allowed index/pattern."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Allowed index pattern OR concrete index matching allowlist."},
            "max_fields": {"type": "integer", "minimum": 50, "maximum": 2000, "default": 400}
        },
        "required": ["pattern"]
    }

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        pattern = args["pattern"].strip()
        if not _is_allowed_source(pattern, ctx.settings.es_allowed_patterns):
            return {"error": f"Index/pattern not allowed. Allowed patterns: {ctx.settings.es_allowed_patterns}"}

        max_fields = int(args.get("max_fields", 400))
        fields, date_fields = await ctx.schema_cache.get_flat_fields(pattern)

        def has_any(s: str, words: List[str]) -> bool:
            s = s.lower()
            return any(w in s for w in words)

        # ✅ Actor/adversary-ish fields (helps "top actors" even before enrichment exists)
        actor_fields = [
            f for f, t in fields.items()
            if has_any(f, [
                "threat.actor", "threat_actor", "threatactor",
                "actor", "adversary", "intrusion", "intrusion_set",
                "apt", "group", "campaign", "cluster",
                "threat.group", "threat.group.name"
            ]) and t in ("keyword", "text")
        ]

        cve_fields = [f for f, t in fields.items() if has_any(f, ["cve"]) and t in ("keyword", "text")]
        message_fields = [
            f for f, t in fields.items()
            if has_any(f, ["message", "description", "summary", "title", "event.original", "log.original"])
            and t in ("keyword", "text")
        ]
        user_fields = [f for f, t in fields.items() if has_any(f, ["user", "username", "account", "principal"]) and t in ("keyword", "text")]
        host_fields = [f for f, t in fields.items() if has_any(f, ["host", "device", "endpoint"]) and t in ("keyword", "text")]
        ip_fields = [f for f, t in fields.items() if has_any(f, ["ip", "src", "dst", "source", "destination"]) and t in ("keyword", "text")]

        preferred_time_field = await _pick_time_field_by_probe(
            ctx, pattern, date_fields, ctx.settings.default_time_from, ctx.settings.default_time_to
        )

        priority: List[str] = []
        seen = set()

        def add_list(xs: List[str]) -> None:
            for x in xs:
                if x in fields and x not in seen:
                    priority.append(x)
                    seen.add(x)

        if preferred_time_field:
            add_list([preferred_time_field])
        add_list(date_fields)

        # ✅ Put actor fields early
        add_list(actor_fields)

        add_list(cve_fields)
        add_list(message_fields)
        add_list(user_fields)
        add_list(host_fields)
        add_list(ip_fields)

        for f in sorted(fields.keys()):
            if f not in seen:
                priority.append(f)
                seen.add(f)
            if len(priority) >= max_fields:
                break

        caps_fields = priority[:200]
        caps = await _field_caps_for_fields(ctx, pattern, caps_fields)

        sample_fields = []
        for f in priority[:max_fields]:
            sample_fields.append({
                "field": f,
                "type": fields[f],
                "searchable": bool((caps.get(f) or {}).get("searchable", True)),
                "aggregatable": bool((caps.get(f) or {}).get("aggregatable", fields[f] == "keyword")),
            })

        return {
            "pattern": pattern,
            "field_count": len(fields),
            "preferred_time_field": preferred_time_field,
            "date_fields": date_fields[:30],

            "actor_fields": actor_fields[:120],   # ✅ NEW
            "cve_fields": cve_fields[:80],
            "message_fields": message_fields[:40],
            "user_fields": user_fields[:40],
            "host_fields": host_fields[:30],
            "ip_fields": ip_fields[:40],

            "field_caps": caps,
            "sample_fields": sample_fields,
        }


# -----------------------------
# Tool: esql_query (auto-detect real time field by probe, not @timestamp)
# -----------------------------
class EsqlQueryTool(Tool):
    name = "esql_query"
    description = (
        "Run an ES|QL query safely. Must use FROM <allowed pattern> or a concrete index matching allowlist. "
        "A time-range filter is applied via Query DSL 'filter' unless omitted."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "ES|QL query string. Example: FROM logs-* | KEEP message"},
            "time_field": {"type": "string", "description": "Date field for time filter (optional)."},
            "time_from": {"type": "string", "description": "Start (date math or ISO), e.g. now-6h"},
            "time_to": {"type": "string", "description": "End (date math or ISO), e.g. now"},
            "filter": {"type": "object", "description": "Extra Query DSL filter object to AND with time filter."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Row limit (hard-capped by ROW_LIMIT)."}
        },
        "required": ["query"]
    }

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        raw_query = args["query"].strip()
        src = _extract_esql_from_source(raw_query)
        if not src:
            return {"error": "ES|QL query must include FROM <index-pattern>."}

        if not _is_allowed_source(src, ctx.settings.es_allowed_patterns):
            return {"error": f"FROM source '{src}' not allowed. Allowed patterns: {ctx.settings.es_allowed_patterns}"}

        time_field = (args.get("time_field") or "").strip() or None
        time_from = args.get("time_from") or ctx.settings.default_time_from
        time_to = args.get("time_to") or ctx.settings.default_time_to
        extra_filter = args.get("filter")

        source_str = src.strip().strip('"').strip("'")
        pattern_for_cache = source_str if source_str in ctx.settings.es_allowed_patterns else None
        if not pattern_for_cache:
            for pat in ctx.settings.es_allowed_patterns:
                if fnmatch.fnmatch(source_str, pat):
                    pattern_for_cache = pat
                    break

        fields_map, date_fields = await ctx.schema_cache.get_flat_fields(pattern_for_cache or source_str)

        if (not time_field) or (fields_map.get(time_field) not in ("date", "date_nanos")):
            time_field = await _pick_time_field_by_probe(ctx, source_str, date_fields, time_from, time_to)

        safe_query = _enforce_limit(raw_query, ctx.settings.row_limit, args.get("limit"))
        dsl_filter = _build_filter(time_field, time_from, time_to, extra_filter)

        resp = await ctx.es.esql.query(query=safe_query, filter=dsl_filter, format="json")

        columns = resp.get("columns", []) or []
        values = resp.get("values", []) or []

        col_names = [c.get("name", "") for c in columns]
        values = values[: ctx.settings.row_limit]

        return {
            "query": safe_query,
            "filter": dsl_filter,
            "time_field_used": time_field,
            "took": resp.get("took"),
            "columns": col_names,
            "rows": values,
            "row_count": len(values),
        }


# -----------------------------
# Tool: dsl_search (tolerant + agg-friendly + returns aggregations)
# -----------------------------
class DslSearchTool(Tool):
    name = "dsl_search"
    description = (
        "Run an Elasticsearch Query DSL _search against an allowed index/pattern. "
        "Provide either 'query' (query clause) or 'body' (full request). "
        "If neither is provided, it defaults to match_all."
    )
    parameters = {
        "type": "object",
        "properties": {
            "index": {"type": "string", "description": "Concrete index or allowed pattern."},
            "query": {"type": "object", "description": "Query clause OR full body (auto-detect). Optional."},
            "body": {"type": "object", "description": "Full _search body. Optional alternative to 'query'."},
            "size": {"type": "integer", "minimum": 0, "maximum": 200, "default": 10},  # allow 0 for aggs/count
            "_source": {"type": "array", "items": {"type": "string"}, "description": "Optional _source fields."}
        },
        "required": ["index"]
    }

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        index = args["index"].strip()
        if not _is_allowed_source(index, ctx.settings.es_allowed_patterns):
            return {"error": f"Index '{index}' not allowed. Allowed patterns: {ctx.settings.es_allowed_patterns}"}

        size = int(args.get("size", 10))
        size = max(0, min(size, 200))
        if size > 0:
            size = min(size, ctx.settings.row_limit)

        body = args.get("body")
        q = args.get("query")

        if body is None and q is None:
            q = {"match_all": {}}

        if body is None:
            if isinstance(q, dict) and any(k in q for k in ("query", "aggs", "sort", "size", "_source", "track_total_hits")):
                body = q
            else:
                body = {"query": q}

        body["size"] = size
        # IMPORTANT: allow caller to request accurate totals
        body.setdefault("track_total_hits", False)

        if args.get("_source"):
            body["_source"] = args["_source"]

        resp = await ctx.es.search(index=index, body=body)
        # elasticsearch client may return ObjectApiResponse; body still acts like mapping, but be safe:
        resp = getattr(resp, "body", resp)

        total = ((resp.get("hits") or {}).get("total") or {})  # {value, relation} when tracked
        hits = ((resp.get("hits") or {}).get("hits") or []) if size > 0 else []

        out_hits = []
        for h in hits[:size]:
            out_hits.append({
                "_index": h.get("_index"),
                "_id": h.get("_id"),
                "_score": h.get("_score"),
                "_source": h.get("_source"),
            })

        return {
            "index": index,
            "dsl": body,
            "took": resp.get("took"),
            "total": total,                                # ✅ NEW: used for exact counts
            "count": len(out_hits),
            "hits": out_hits,
            "aggregations": resp.get("aggregations") or {}, # ✅ needed for top-N/metrics
        }







# -----------------------------
# Tool: get_doc
# -----------------------------
class GetDocTool(Tool):
    name = "get_doc"
    description = "Fetch a single document by index and _id (for 'show me full event')."
    parameters = {
        "type": "object",
        "properties": {
            "index": {"type": "string", "description": "Concrete index (must match allowlist patterns)."},
            "id": {"type": "string", "description": "Document _id."}
        },
        "required": ["index", "id"]
    }

    async def run(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        index = args["index"].strip()
        if not _is_allowed_source(index, ctx.settings.es_allowed_patterns):
            return {"error": f"Index '{index}' not allowed."}
        doc = await ctx.es.get(index=index, id=args["id"])
        return {"_index": doc.get("_index"), "_id": doc.get("_id"), "_source": doc.get("_source")}

