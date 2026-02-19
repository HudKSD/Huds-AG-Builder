# agent.py
import json
import re
from collections import Counter
from typing import Any, Dict, List, Tuple, Optional

from .llm_client import OpenAICompatibleClient, LLMError
from .conversation_store import ConversationStore
from .tools.registry import ToolRegistry


SYSTEM_PROMPT = """You are an Elasticsearch SOC data assistant.

You MUST use discovered schema and real Elasticsearch results.
Do NOT invent fields or results.

Execution engines:
- Use Query DSL for: full-text search, filters, aggregations (top N, group-by terms, metrics), and COUNTS.
- Use ES|QL for: pipeline analytics / tabular transformations (STATS, EVAL, KEEP, SORT, LIMIT) and time-series tables.

Rules:
- Schema-first is mandatory (we always fetch mappings/field_caps before searching).
- Never assume fields like 'cve_id' or '@timestamp' exist.
- Use type-aware logic:
  - term/terms on keyword-like fields
  - match/match_phrase on text fields
  - for aggregations, prefer '.keyword' when present
- Always apply a time window if possible, but if no results: use fallback ladder.
- When done, answer with a high-quality analyst report:
  1) Executive summary (2-4 bullets, plain language)
  2) Key findings (evidence-backed bullets; quote concrete fields/values)
  3) Assumptions & filters (engine used, time field used, time range, index pattern)
  4) Suggested next checks (2-4 actionable follow-up queries)
- Prefer concise but thorough explanations similar to high-quality SOC analyst notes.
"""

PLANNER_PROMPT = """You are a STRICT Elasticsearch query planner.

Return ONLY valid JSON (no markdown, no commentary).
Use ONLY fields present in schema.sample_fields / schema.cve_fields / schema.message_fields / schema.date_fields
(and schema.actor_fields if present). If unsure, prefer text_query and do NOT guess fields.

Plan JSON schema:
{
  "pattern": "<MUST equal schema.pattern>",
  "mode": "search" | "aggregate",
  "limit": <int 1..200>,
  "include_older_if_none": true | false,

  "time": {
    "enabled": true | false,
    "field": "<optional date field>",
    "from": "<date math or ISO, e.g. now-30d>",
    "to": "<date math or ISO, e.g. now>"
  },

  "filters": [
    {"field": "<field>", "op": "eq|neq|contains|match|match_phrase|gt|gte|lt|lte|exists|in", "value": "<string|number|bool|array>"}
  ],

  "text_query": "<optional string>",

  "group_by": "<field for group-by (terms) (optional)>",
  "metric": {"fn": "count|count_distinct|avg|sum|min|max", "field": "<optional>"}
}

Guidance:
- If user asks for a specific indicator (CVE/IP/hash/domain), set mode="search" and set text_query to that indicator.
- If user asks "top X", "most common", "group by", or "count by", set mode="aggregate" and group_by if possible.
"""

ESQL_WRITER_PROMPT = """You are an Elasticsearch ES|QL query writer.

Output ONLY an ES|QL query (no markdown, no commentary).

Rules:
- Must start with: FROM {pattern}
- Use ONLY the provided fields.
- Do NOT add time-range filtering (it is applied externally).
- Use ES|QL pipeline commands to answer the question: WHERE, EVAL, STATS, KEEP, SORT, LIMIT.
- For time series tables, use DATE_TRUNC(<interval>, <time_field>) and STATS ... BY <bucket>, then SORT <bucket>.
- Always include a LIMIT (<= {row_limit}).
"""

ESQL_REPAIR_PROMPT = """You are fixing an Elasticsearch ES|QL query.

Return ONLY the corrected ES|QL query (no markdown, no commentary).

Constraints:
- Must start with: FROM {pattern}
- Use ONLY allowed fields.
- Do NOT add time-range filtering (external).
- Keep it simple and ensure it parses.
- Always include LIMIT (<= {row_limit}).
"""


# ---- Context safety limits ----
MAX_CONTEXT_CHARS = 180_000
MAX_TOOL_MESSAGE_CHARS = 12_000
MAX_TABLE_ROWS_FOR_LLM = 30
MAX_CELL_CHARS = 240
MAX_DICT_KEYS = 20
MAX_LIST_ITEMS = 30


def _clip_text(s: str, n: int) -> str:
    s = s if s is not None else ""
    return s if len(s) <= n else s[:n] + "…(truncated)"


def _clip_any(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        return _clip_text(v, 2000)
    if isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, list):
        out = [_clip_any(x) for x in v[:MAX_LIST_ITEMS]]
        if len(v) > MAX_LIST_ITEMS:
            out.append(f"…(truncated {len(v) - MAX_LIST_ITEMS} items)")
        return out
    if isinstance(v, dict):
        keys = list(v.keys())[:MAX_DICT_KEYS]
        out = {k: _clip_any(v[k]) for k in keys}
        if len(v) > MAX_DICT_KEYS:
            out["…"] = f"(truncated {len(v) - MAX_DICT_KEYS} keys)"
        return out
    return _clip_text(str(v), 2000)


def _sanitize_tool_result(tool_name: str, result: Any) -> Any:
    if isinstance(result, dict) and "error" in result:
        return {"error": _clip_text(str(result["error"]), 2000)}

    if not isinstance(result, dict):
        return _clip_any(result)

    if tool_name == "esql_query":
        cols = (result.get("columns") or [])[:80]
        rows = (result.get("rows") or [])[:MAX_TABLE_ROWS_FOR_LLM]
        safe_rows = []
        for r in rows:
            rr = []
            for cell in (r or [])[:len(cols)]:
                if cell is None:
                    rr.append(None)
                else:
                    s = str(cell)
                    rr.append(s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…")
            safe_rows.append(rr)

        return {
            "query": _clip_text(result.get("query", ""), 6000),
            "filter": _clip_any(result.get("filter")),
            "time_field_used": result.get("time_field_used"),
            "took": result.get("took"),
            "columns": cols,
            "rows": safe_rows,
            "row_count": len(safe_rows),
        }

    if tool_name == "dsl_search":
        hits = (result.get("hits") or [])[:10]
        out_hits = []
        for h in hits:
            src = h.get("_source")
            if isinstance(src, dict):
                keys = list(src.keys())[:12]
                src_small = {k: _clip_any(src[k]) for k in keys}
                if len(src) > 12:
                    src_small["…"] = f"(truncated {len(src) - 12} keys)"
            else:
                src_small = _clip_text(str(src), 2000)

            out_hits.append({
                "_index": h.get("_index"),
                "_id": h.get("_id"),
                "_score": h.get("_score"),
                "_source": src_small,
            })

        return {
            "index": result.get("index"),
            "dsl": _clip_any(result.get("dsl")),
            "count": len(out_hits),
            "hits": out_hits,
            "stage": result.get("stage"),
            "time_field_used": result.get("time_field_used"),
            "aggregations": _clip_any(result.get("aggregations") or {}),
        }

    if tool_name == "get_mappings":
        out = {
            "pattern": result.get("pattern"),
            "field_count": result.get("field_count"),
            "preferred_time_field": result.get("preferred_time_field"),
            "date_fields": (result.get("date_fields") or [])[:30],
            "cve_fields": (result.get("cve_fields") or [])[:80],
            "message_fields": (result.get("message_fields") or [])[:40],
            "sample_fields": (result.get("sample_fields") or [])[:220],
            "field_caps": (result.get("field_caps") or {}),
        }
        if "actor_fields" in result:
            out["actor_fields"] = (result.get("actor_fields") or [])[:120]
        return out

    return _clip_any(result)


def _message_size(m: Dict[str, Any]) -> int:
    s = ""
    if "content" in m and isinstance(m["content"], str):
        s += m["content"]
    if "tool_calls" in m:
        s += json.dumps(m["tool_calls"], ensure_ascii=False)
    return len(s)


def _chunk_for_tool_calls(rest: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    blocks: List[List[Dict[str, Any]]] = []
    i = 0
    while i < len(rest):
        m = rest[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            block = [m]
            i += 1
            while i < len(rest) and rest[i].get("role") == "tool":
                block.append(rest[i])
                i += 1
            blocks.append(block)
        else:
            blocks.append([m])
            i += 1
    return blocks


def _repair_orphan_tool_messages(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in msgs:
        if m.get("role") == "tool":
            if not out or out[-1].get("role") != "assistant" or not out[-1].get("tool_calls"):
                out.append({"role": "assistant", "content": f"[orphan_tool_output]\n{m.get('content','')}"})
            else:
                out.append(m)
        else:
            out.append(m)
    return out


def _trim_context(messages: List[Dict[str, Any]], max_chars: int = MAX_CONTEXT_CHARS) -> List[Dict[str, Any]]:
    if not messages:
        return messages

    system = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system else messages[:]

    blocks = _chunk_for_tool_calls(rest)

    kept_blocks: List[List[Dict[str, Any]]] = []
    total = _message_size(system) if system else 0

    for block in reversed(blocks):
        block_size = sum(_message_size(m) for m in block)
        if total + block_size > max_chars and kept_blocks:
            break
        if total + block_size > max_chars and not kept_blocks:
            clipped = []
            for mm in block:
                m2 = dict(mm)
                if isinstance(m2.get("content"), str):
                    m2["content"] = _clip_text(m2["content"], max_chars // 6)
                clipped.append(m2)
            kept_blocks.append(clipped)
            total += sum(_message_size(m) for m in clipped)
            break

        kept_blocks.append(block)
        total += block_size

    kept_blocks.reverse()
    flattened: List[Dict[str, Any]] = []
    for b in kept_blocks:
        flattened.extend(b)

    final_msgs = ([system] + flattened) if system else flattened
    return _repair_orphan_tool_messages(final_msgs)


def _extract_tool_calls(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    tc = msg.get("tool_calls")
    if isinstance(tc, list):
        return tc
    fc = msg.get("function_call")
    if fc and isinstance(fc, dict):
        return [{
            "id": "legacy",
            "type": "function",
            "function": {"name": fc.get("name"), "arguments": fc.get("arguments", "{}")}
        }]
    return []


# -------------------------
# Indicator + time parsing
# -------------------------
CVE_IND_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")


def _detect_indicator(text: str) -> Optional[Dict[str, str]]:
    t = text.strip()
    m = CVE_IND_RE.search(t)
    if m:
        return {"type": "cve", "value": m.group(0).upper()}
    m = SHA256_RE.search(t)
    if m:
        return {"type": "sha256", "value": m.group(0).lower()}
    m = SHA1_RE.search(t)
    if m:
        return {"type": "sha1", "value": m.group(0).lower()}
    m = MD5_RE.search(t)
    if m:
        return {"type": "md5", "value": m.group(0).lower()}
    m = IP_RE.search(t)
    if m:
        return {"type": "ip", "value": m.group(0)}
    m = DOMAIN_RE.search(t)
    if m and " " not in m.group(0):
        return {"type": "domain", "value": m.group(0).lower()}
    return None


def _extract_time_window(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = text.lower()
    if "last week" in t or "past week" in t:
        return ("now-7d", "now")
    if "last month" in t or "past month" in t:
        return ("now-30d", "now")
    if "last year" in t or "past year" in t:
        return ("now-365d", "now")

    m = re.search(r"(last|past)\s+(\d+)\s*(day|days|d)\b", t)
    if m:
        return (f"now-{int(m.group(2))}d", "now")
    m = re.search(r"(last|past)\s+(\d+)\s*(hour|hours|h)\b", t)
    if m:
        return (f"now-{int(m.group(2))}h", "now")
    m = re.search(r"(last|past)\s+(\d+)\s*(minute|minutes|min|mins|m)\b", t)
    if m:
        return (f"now-{int(m.group(2))}m", "now")

    return (None, None)


def _extract_esql_interval(text: str) -> Optional[str]:
    t = text.lower()
    if "per hour" in t or "hourly" in t:
        return "1 hour"
    if "per day" in t or "daily" in t:
        return "1 day"
    if "per week" in t or "weekly" in t:
        return "1 week"
    if "per month" in t or "monthly" in t:
        return "1 month"
    return None


# -------------------------
# Type-aware helpers
# -------------------------
NUMERIC_TYPES = {"byte", "short", "integer", "long", "half_float", "float", "double", "scaled_float", "unsigned_long"}
KEYWORD_TYPES = {"keyword", "ip", "boolean"}
TEXT_TYPES = {"text", "match_only_text", "wildcard"}


def _norm_field(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _resolve_field(name: str, fields_map: Dict[str, str]) -> Optional[str]:
    if not name:
        return None
    n = name.strip()
    if n in fields_map:
        return n
    if n.endswith(".keyword") and n[:-8] in fields_map:
        return n[:-8]
    if (n + ".keyword") in fields_map:
        return n + ".keyword"
    alt = n.replace("_", ".")
    if alt in fields_map:
        return alt
    if (alt + ".keyword") in fields_map:
        return alt + ".keyword"
    nn = _norm_field(n)
    for f in fields_map.keys():
        if _norm_field(f) == nn:
            return f
    return None


def _prefer_keyword(field: str, fields_map: Dict[str, str]) -> str:
    if field in fields_map and fields_map[field] in TEXT_TYPES:
        kw = field + ".keyword"
        if kw in fields_map and fields_map[kw] == "keyword":
            return kw
    return field


def _make_clause(field: str, op: str, value: Any, fields_map: Dict[str, str]) -> Optional[Dict[str, Any]]:
    f = _resolve_field(field, fields_map)
    if not f:
        return None
    ftype = fields_map.get(f, "object")

    if op == "exists":
        return {"exists": {"field": f}}

    if op in ("gt", "gte", "lt", "lte"):
        if ftype in NUMERIC_TYPES or ftype in ("date", "date_nanos"):
            return {"range": {f: {op: value}}}
        return None

    if op == "in":
        if not isinstance(value, list):
            return None
        ff = _prefer_keyword(f, fields_map) if fields_map.get(f) in TEXT_TYPES else f
        return {"terms": {ff: value}}

    if op in ("eq", "neq"):
        ff = _prefer_keyword(f, fields_map) if fields_map.get(f) in TEXT_TYPES else f
        if fields_map.get(ff) == "keyword" or fields_map.get(ff) in KEYWORD_TYPES or fields_map.get(ff) in NUMERIC_TYPES or fields_map.get(ff) in ("date", "date_nanos"):
            clause = {"term": {ff: value}}
        else:
            clause = {"match_phrase": {f: {"query": str(value)}}}
        if op == "neq":
            return {"bool": {"must_not": [clause]}}
        return clause

    if op == "contains":
        if ftype == "keyword" or ftype in KEYWORD_TYPES:
            return {"wildcard": {f: {"value": f"*{value}*", "case_insensitive": True}}}
        return {"match": {f: {"query": str(value), "operator": "and"}}}

    if op == "match":
        return {"match": {f: {"query": str(value), "operator": "and"}}}

    if op == "match_phrase":
        return {"match_phrase": {f: {"query": str(value)}}}

    return None


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _extract_esql_from_llm(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    m = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", t)
    if m:
        return m.group(1).strip()
    return t.strip().strip('"').strip("'")


async def _pick_time_field_by_probe(agent: "NL2ESAgent", index: str, candidates: List[str], gte: str, lte: str) -> Optional[str]:
    es = agent.tools.ctx.es
    for f in candidates[:12]:
        try:
            r = await es.count(index=index, query={"range": {f: {"gte": gte, "lte": lte}}})
            r = getattr(r, "body", r)
            if (r or {}).get("count", 0) > 0:
                return f
        except Exception:
            continue
    return candidates[0] if candidates else None


# -------------------------
# Router helpers
# -------------------------
_ANALYTICS_HINTS = [
    "trend", "over time", "time series", "timeseries", "timeline",
    "per day", "per hour", "hourly", "daily", "weekly", "monthly",
    "rate", "error rate", "ratio", "percentage", "percent",
    "average", "avg", "sum", "min", "max", "median", "percentile",
    "stdev", "std dev", "standard deviation", "variance",
    "evaluate", "eval", "calculate", "compute", "statistics", "stats",
    "histogram", "distribution",
    "table", "tabular"
]

_SEARCH_HINTS = [
    "find", "search", "show documents", "show docs", "list documents",
    "latest", "recent", "display documents", "mentions", "where"
]


def _should_use_esql(text: str) -> bool:
    t = text.lower()
    # Keep counts and top-N in DSL.
    if _is_countish(t):
        return False
    if ("top " in t) or ("most common" in t) or ("most frequent" in t) or ("most mentioned" in t):
        return False
    if any(h in t for h in _SEARCH_HINTS):
        return False
    if any(h in t for h in _ANALYTICS_HINTS):
        return True
    if re.search(r"\b(per|by)\s+(day|hour|week|month)\b", t):
        return True
    return False


# -------------------------
# Top-N helper
# -------------------------
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
              "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _extract_top_n(text: str, default: int = 5) -> int:
    t = text.lower()
    m = re.search(r"\btop\s+(\d{1,3})\b", t)
    if m:
        return max(1, min(int(m.group(1)), 50))
    m = re.search(r"\btop\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b", t)
    if m:
        return _NUM_WORDS.get(m.group(1), default)
    return default


def _is_top_question(text: str) -> bool:
    t = text.lower()
    return ("top " in t) or ("most common" in t) or ("most frequent" in t) or ("most mentioned" in t)


def _detect_top_entity(text: str) -> Optional[str]:
    t = text.lower()
    if any(k in t for k in ["threat actor", "threat actors", "adversary", "adversaries", "intrusion set", "apt", "threat group"]):
        return "actors"
    if "cve" in t or "vulnerab" in t:
        return "cves"
    if re.search(r"\bips?\b", t) or (" ip " in f" {t} "):
        return "ips"
    if "domain" in t or "domains" in t or "url" in t or "hostname" in t:
        return "domains"
    if "hash" in t or "sha256" in t or "sha1" in t or "md5" in t:
        return "hashes"
    return None


# -------------------------
# COUNT detection + follow-up memory
# -------------------------
COUNT_RE = re.compile(r"\b(how\s*many|howmany|count|number\s+of|total)\b", re.IGNORECASE)
FOLLOWUP_COUNT_RE = re.compile(r"^\s*(give\s+me\s+)?(the\s+)?count\s*$", re.IGNORECASE)
HOWABOUT_RE = re.compile(r"^\s*(how\s+about|what\s+about)\b", re.IGNORECASE)

def _is_countish(text: str) -> bool:
    return bool(COUNT_RE.search(text)) or bool(FOLLOWUP_COUNT_RE.search(text))

def _infer_topic(text: str) -> Optional[str]:
    t = text.lower()
    if "cve" in t:
        return "cves"
    if "ransomware" in t:
        return "ransomware"
    if any(x in t for x in ["threat actor", "adversary", "intrusion set", "apt", "threat group"]):
        return "actors"
    return None

def _detect_count_target(text: str) -> Optional[str]:
    t = text.lower()
    if "ransomware" in t:
        return "ransomware"
    if "cve" in t:
        return "cves"
    if "event" in t or "document" in t or "doc" in t:
        return "documents"
    return None


def _extract_index_hint(text: str) -> Optional[str]:
    t = text.lower()
    patterns = [
        r"\b(?:in|from)\s+(?:the\s+)?([a-z0-9._*?-]+)\s+index\b",
        r"\b([a-z0-9._*?-]+)\s+index\b",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            return (m.group(1) or "").strip()
    return None


def _field_tokens(name: str) -> set[str]:
    return set(x for x in re.split(r"[^a-z0-9]+", name.lower()) if x)


def _semantic_candidates(schema_small: Dict[str, Any], concept: str) -> List[str]:
    token_map = {
        "actors": {"actor", "actors", "adversary", "threat", "group", "intrusion", "apt", "campaign"},
        "cves": {"cve", "vulnerability", "vuln"},
        "ips": {"ip", "src", "dst", "source", "destination", "client", "server"},
        "domains": {"domain", "dns", "host", "hostname", "url", "fqdn"},
        "hashes": {"hash", "sha1", "sha256", "md5", "fingerprint"},
    }
    wanted = token_map.get(concept, set())
    if not wanted:
        return []

    scored: List[Tuple[int, str]] = []
    for f in schema_small.get("sample_fields") or []:
        if not isinstance(f, dict):
            continue
        name = str(f.get("field") or "")
        if not name:
            continue
        if not bool(f.get("aggregatable", False)):
            continue
        toks = _field_tokens(name)
        overlap = len(toks & wanted)
        if overlap <= 0:
            continue
        scored.append((overlap, name))

    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return [name for _, name in scored[:20]]

def _has_time_phrase(text: str) -> bool:
    tf, _ = _extract_time_window(text)
    return tf is not None


class NL2ESAgent:
    def __init__(self, llm: OpenAICompatibleClient, tools: ToolRegistry, store: ConversationStore, max_steps: int = 8):
        self.llm = llm
        self.tools = tools
        self.store = store
        self.max_steps = max_steps
        # lightweight session state for follow-ups (per conversation_id)
        self._state: Dict[str, Dict[str, Any]] = {}

    def _state_get(self, conversation_id: str) -> Dict[str, Any]:
        return self._state.setdefault(conversation_id, {})

    def _state_update(self, conversation_id: str, **kwargs: Any) -> None:
        st = self._state_get(conversation_id)
        st.update(kwargs)

    async def _resolve_pattern_from_hint(self, hint: Optional[str]) -> Optional[str]:
        if not hint:
            return None
        allowed = self.tools.ctx.settings.es_allowed_patterns
        # Exact/prefix substring score over allowlist entries (deterministic, no LLM)
        scored = []
        for p in allowed:
            ps = p.lower()
            hs = hint.lower()
            score = 0
            if hs == ps:
                score += 100
            if hs in ps:
                score += 30
            if ps.startswith(hs):
                score += 40
            if hs.rstrip('*') and hs.rstrip('*') in ps:
                score += 20
            scored.append((score, p))
        scored.sort(key=lambda x: (-x[0], len(x[1])))
        best = scored[0][1] if scored and scored[0][0] > 0 else None
        return best


    def _trace_reasoning(self, trace: List[Dict[str, Any]], text: str) -> None:
        trace.append({"type": "reasoning", "reasoning": text})

    def _trace_tool_call(
        self,
        trace: List[Dict[str, Any]],
        tool_id: str,
        params: Dict[str, Any],
        result: Any,
        progression: Optional[List[str]] = None,
    ) -> None:
        trace.append({
            "type": "tool_call",
            "tool_id": tool_id,
            "progression": [{"message": m} for m in (progression or [])],
            "params": _clip_any(params),
            "result": _clip_any(result),
        })

    def _score_index_name(self, hint: str, name: str) -> int:
        hs = hint.lower().strip()
        ns = name.lower().strip()
        score = 0
        if hs == ns:
            score += 120
        if ns.startswith(hs):
            score += 60
        if hs in ns:
            score += 40
        h0 = hs.rstrip("*?")
        if h0 and h0 in ns:
            score += 20
        return score

    async def _resolve_index_from_hint_by_listing(self, hint: str, trace: List[Dict[str, Any]]) -> Optional[str]:
        self._trace_reasoning(trace, f"Trying to resolve user index hint '{hint}' by listing allowed patterns.")
        res = await self.tools.run("resolve_index", {"hint": hint, "max_results": 500})
        self._trace_tool_call(
            trace,
            tool_id="resolve_index",
            params={"hint": hint, "max_results": 500},
            result=res,
            progression=["Identifying the most relevant data source"],
        )
        if isinstance(res, dict):
            best = (res.get("best_index") or "").strip() or None
            if best:
                self._trace_reasoning(trace, f"Resolved hint '{hint}' to concrete index '{best}'.")
                return best
        self._trace_reasoning(trace, f"Could not confidently resolve hint '{hint}' to a concrete index.")
        return None

    # -------------------------
    # Schema-first
    # -------------------------
    async def _schema_first(self, messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str, List[Dict[str, Any]]]:
        trace: List[Dict[str, Any]] = []
        allowed = self.tools.ctx.settings.es_allowed_patterns
        hint = _extract_index_hint((messages[-1].get("content") or "") if messages else "")

        selected_pattern: Optional[str] = None
        if hint:
            self._trace_reasoning(trace, f"User asked for index hint '{hint}'. Trying deterministic index resolution first.")
            selected_pattern = await self._resolve_index_from_hint_by_listing(hint, trace)
            if not selected_pattern:
                selected_pattern = await self._resolve_pattern_from_hint(hint)
                if selected_pattern:
                    self._trace_reasoning(trace, f"Hint matched allowlisted pattern '{selected_pattern}'.")

        if not selected_pattern and len(allowed) == 1:
            selected_pattern = allowed[0]
            self._trace_reasoning(trace, f"Only one allowed pattern '{selected_pattern}', selecting it deterministically.")

        if not selected_pattern:
            schema_prompt = (
                "MANDATORY STEP (schema-first): Choose ONE best index pattern from this allowlist:\n"
                f"{allowed}\n"
                + (f"User index hint: {hint}. Prefer it if valid.\n" if hint else "")
                + "Then CALL get_mappings(pattern=<chosen pattern>) EXACTLY ONCE.\n"
                + "Do NOT call any other tool in this step."
            )

            only_get_mappings = [t for t in self.tools.schemas() if (t.get("function") or {}).get("name") == "get_mappings"]
            schema_messages = _trim_context(messages + [{"role": "system", "content": schema_prompt}], max_chars=90_000)

            resp = await self.llm.chat(messages=schema_messages, tools=only_get_mappings, tool_choice="auto")
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            tool_calls = _extract_tool_calls(msg)

            if not tool_calls or (tool_calls[0].get("function") or {}).get("name") != "get_mappings":
                selected_pattern = allowed[0]
                tool_calls = [{
                    "id": "schema0",
                    "type": "function",
                    "function": {"name": "get_mappings", "arguments": json.dumps({"pattern": selected_pattern, "max_fields": 400})}
                }]
                messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
                self._trace_reasoning(trace, f"LLM did not choose get_mappings; fallback to first allowlisted pattern '{selected_pattern}'.")
            else:
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
                try:
                    args = json.loads((tool_calls[0].get("function") or {}).get("arguments") or "{}")
                except Exception:
                    args = {"pattern": allowed[0], "max_fields": 400}
                selected_pattern = (args.get("pattern") or allowed[0]).strip()

            fn = tool_calls[0]["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {"pattern": selected_pattern, "max_fields": 400}
            selected_pattern = (args.get("pattern") or selected_pattern or allowed[0]).strip()
        else:
            args = {"pattern": selected_pattern, "max_fields": 400}
            tool_calls = [{
                "id": "schema0",
                "type": "function",
                "function": {"name": "get_mappings", "arguments": json.dumps(args)}
            }]
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})

        self._trace_tool_call(
            trace,
            tool_id="get_mappings",
            params=args,
            result={"note": "executing"},
            progression=["Discovering schema before search"],
        )
        result_full = await self.tools.run("get_mappings", args)
        result_small = _sanitize_tool_result("get_mappings", result_full)
        tool_content = _clip_text(json.dumps(result_small, ensure_ascii=False), MAX_TOOL_MESSAGE_CHARS)

        # update last tool_call entry with actual result
        if trace and trace[-1].get("type") == "tool_call" and trace[-1].get("tool_id") == "get_mappings":
            trace[-1]["result"] = _clip_any(result_small)

        messages.append({"role": "tool", "tool_call_id": tool_calls[0].get("id", "schema0"), "content": tool_content})
        trace.append({"step": "schema_first", "pattern": selected_pattern})
        return messages, result_small, selected_pattern, trace

    # -------------------------
    # COUNT engine (exact) + follow-ups
    # -------------------------
    async def _count_docs(
        self,
        pattern: str,
        query: Dict[str, Any],
        time_field: Optional[str] = None,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
        trace: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        args: Dict[str, Any] = {"index": pattern, "query": query}
        if time_field:
            args["time_field"] = time_field
        if time_from:
            args["time_from"] = time_from
        if time_to:
            args["time_to"] = time_to

        res = await self.tools.run("count_docs", args)
        if trace is not None:
            self._trace_tool_call(
                trace,
                tool_id="count_docs",
                params=args,
                result=res,
                progression=["Computing exact count"],
            )
        if isinstance(res, dict) and isinstance(res.get("count"), int):
            return int(res.get("count") or 0)
        return 0

    async def _unique_terms_count_composite(
        self,
        pattern: str,
        field: str,
        time_field: Optional[str],
        time_from: str,
        time_to: str,
        max_pages: int = 3000
    ) -> Tuple[int, bool]:
        """
        Exact unique count using composite aggregation paging.
        Returns (count, exact_flag). If too many pages, returns best-effort and exact_flag=False.
        """
        after = None
        total = 0
        pages = 0

        filt: List[Dict[str, Any]] = []
        if time_field:
            filt.append({"range": {time_field: {"gte": time_from, "lte": time_to}}})
        filt.append({"exists": {"field": field}})

        while True:
            comp: Dict[str, Any] = {
                "size": 1000,
                "sources": [{"v": {"terms": {"field": field}}}],
            }
            if after:
                comp["after"] = after

            body = {
                "size": 0,
                "query": {"bool": {"filter": filt}},
                "aggs": {"u": {"composite": comp}},
                "track_total_hits": False
            }

            r = await self.tools.ctx.es.search(index=pattern, body=body)
            r = getattr(r, "body", r)

            u = ((r.get("aggregations") or {}).get("u") or {})
            buckets = u.get("buckets") or []
            total += len(buckets)

            after = u.get("after_key")
            pages += 1

            if not buckets or not after:
                return total, True

            if pages >= max_pages:
                return total, False

    async def _pick_best_time_field(self, pattern: str, schema_small: Dict[str, Any], time_from: str, time_to: str) -> Optional[str]:
        fields_map, date_fields = await self.tools.ctx.schema_cache.get_flat_fields(pattern)
        tf = schema_small.get("preferred_time_field")
        if tf and fields_map.get(tf) in ("date", "date_nanos"):
            # probe for this window
            try:
                r = await self.tools.ctx.es.count(index=pattern, query={"range": {tf: {"gte": time_from, "lte": time_to}}})
                r = getattr(r, "body", r)
                if (r or {}).get("count", 0) > 0:
                    return tf
            except Exception:
                pass

        # probe date fields
        for f in (date_fields or [])[:12]:
            try:
                r = await self.tools.ctx.es.count(index=pattern, query={"range": {f: {"gte": time_from, "lte": time_to}}})
                r = getattr(r, "body", r)
                if (r or {}).get("count", 0) > 0:
                    return f
            except Exception:
                continue

        return (date_fields or [None])[0]

    async def _handle_count_request(
        self,
        conversation_id: str,
        user_text: str,
        pattern: str,
        schema_small: Dict[str, Any],
        trace: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:

        st = self._state_get(conversation_id)

        # Determine if this is a count request OR a follow-up to a previous count
        is_count = _is_countish(user_text)
        time_in_msg = _has_time_phrase(user_text)

        # follow-up: "how about in the past 1 year" => reuse last_count_target
        if (not is_count) and time_in_msg and HOWABOUT_RE.search(user_text.strip().lower()) and st.get("last_count_target"):
            is_count = True

        # follow-up: message only includes time phrase and last_count_target exists
        if (not is_count) and time_in_msg and st.get("last_count_target") and len(user_text.split()) <= 10:
            is_count = True

        if not is_count:
            return None, None

        # Resolve target: explicit or from state/topic
        target = _detect_count_target(user_text) or st.get("last_count_target") or st.get("last_topic")
        if not target:
            return None, None

        # Resolve time range: explicit or from state or default
        tf, tt = _extract_time_window(user_text)
        time_from = tf or st.get("last_time_from") or "now-7d"
        time_to = tt or st.get("last_time_to") or "now"

        time_field = await self._pick_best_time_field(pattern, schema_small, time_from, time_to)

        # Store time/topic for follow-ups
        self._state_update(conversation_id, last_time_from=time_from, last_time_to=time_to, last_topic=target)

        # ---- Generic documents/events/doc count ----
        if target == "documents":
            query = {"bool": {"filter": [{"range": {time_field: {"gte": time_from, "lte": time_to}}}] if time_field else [],
                              "must": [{"match_all": {}}]}}
            doc_count = await self._count_docs(pattern, query, trace=trace)

            answer = "\n".join([
                "### Document count",
                f"- **Documents matched:** **{doc_count}**",
                "",
                "**Filters used**",
                "- Engine: `DSL`",
                f"- Index pattern: `{pattern}`",
                f"- Time field: `{time_field}`",
                f"- Time range: `{time_from}` → `{time_to}`",
                "- Query: `match_all`",
            ])

            artifact = {
                "index": pattern,
                "dsl": {"query": query},
                "stage": "count_documents",
                "engine": "dsl",
                "time_field_used": time_field,
                "time_range": {"from": time_from, "to": time_to},
                "count": doc_count,
                "hits": [],
            }

            self._state_update(conversation_id, last_count_target="documents")
            trace.append({"step": "count", "target": "documents", "docs": doc_count})
            return answer, artifact

        # ---- RANSOMWARE doc count ----
        if target == "ransomware":
            q = {"simple_query_string": {"query": "ransomware", "fields": ["*"], "default_operator": "and"}}
            query = {"bool": {"filter": [{"range": {time_field: {"gte": time_from, "lte": time_to}}}] if time_field else [],
                              "must": [q]}}

            doc_count = await self._count_docs(pattern, query, trace=trace)

            answer = "\n".join([
                "### Ransomware incidents (document count)",
                f"- **Documents matched:** **{doc_count}**",
                "",
                "**Filters used**",
                "- Engine: `DSL`",
                f"- Index pattern: `{pattern}`",
                f"- Time field: `{time_field}`",
                f"- Time range: `{time_from}` → `{time_to}`",
                "- Query: `ransomware`",
            ])

            artifact = {
                "index": pattern,
                "dsl": {"query": query},
                "stage": "count_ransomware_docs",
                "engine": "dsl",
                "time_field_used": time_field,
                "time_range": {"from": time_from, "to": time_to},
                "count": doc_count,
                "hits": [],
            }

            self._state_update(conversation_id, last_count_target="ransomware")
            trace.append({"step": "count", "target": "ransomware", "docs": doc_count})
            return answer, artifact

        # ---- CVE counts ----
        if target == "cves":
            fields_map, _ = await self.tools.ctx.schema_cache.get_flat_fields(pattern)

            # pick best cve field
            candidates = [
                "entities.cves",
                "entities.cves.keyword",
                "cve_id",
                "cve_id.keyword",
                "cve.keyword",
                "cve",
                "cves.keyword",
                "cves",
            ]

            cve_field = None
            for c in candidates:
                if c in fields_map:
                    # if text, prefer .keyword if available
                    if fields_map.get(c) in TEXT_TYPES and (c + ".keyword") in fields_map:
                        cve_field = c + ".keyword"
                    else:
                        cve_field = c
                    break

            # doc count: docs that contain CVE signal
            if cve_field:
                q_docs = {"exists": {"field": cve_field}}
            else:
                # fallback doc count based on text "CVE-"
                q_docs = {"simple_query_string": {"query": "CVE-", "fields": ["*"], "default_operator": "and"}}

            query_docs = {"bool": {"filter": [{"range": {time_field: {"gte": time_from, "lte": time_to}}}] if time_field else [],
                                   "must": [q_docs]}}
            docs_with_cve = await self._count_docs(pattern, query_docs, trace=trace)

            unique_cves = None
            unique_exact = None

            # exact unique CVEs only if we have a keyword-ish field
            if cve_field and (fields_map.get(cve_field) == "keyword" or cve_field.endswith(".keyword")):
                unique_cves, unique_exact = await self._unique_terms_count_composite(
                    pattern=pattern,
                    field=cve_field,
                    time_field=time_field,
                    time_from=time_from,
                    time_to=time_to,
                    max_pages=3000
                )

            lines = ["### CVE count"]
            if unique_cves is not None:
                lines.append(f"- **Unique CVEs:** **{unique_cves}**" + ("" if unique_exact else " (best-effort; too many pages)"))
            else:
                lines.append("- **Unique CVEs:** not available (requires a structured keyword field like `entities.cves`).")
            lines.append(f"- **Documents with CVE signal:** **{docs_with_cve}**")
            if cve_field:
                lines.append(f"- CVE field used: `{cve_field}`")
            else:
                lines.append("- CVE field not found; used text query `CVE-` (doc count only).")

            lines += [
                "",
                "**Filters used**",
                "- Engine: `DSL`",
                f"- Index pattern: `{pattern}`",
                f"- Time field: `{time_field}`",
                f"- Time range: `{time_from}` → `{time_to}`",
            ]
            answer = "\n".join(lines)

            artifact = {
                "index": pattern,
                "dsl": {"query": query_docs},
                "stage": "count_cves",
                "engine": "dsl",
                "time_field_used": time_field,
                "time_range": {"from": time_from, "to": time_to},
                "count": docs_with_cve,
                "unique_cves": unique_cves,
                "hits": [],
            }

            self._state_update(conversation_id, last_count_target="cves")
            trace.append({"step": "count", "target": "cves", "docs": docs_with_cve, "unique": unique_cves, "field": cve_field})
            return answer, artifact

        return None, None

    # -------------------------
    # Planner (DSL)
    # -------------------------
    async def _plan(self, user_text: str, schema_small: Dict[str, Any], pattern: str) -> Dict[str, Any]:
        indicator = _detect_indicator(user_text)
        t_from, t_to = _extract_time_window(user_text)

        if indicator:
            return {
                "pattern": pattern,
                "mode": "search",
                "limit": min(20, self.tools.ctx.settings.row_limit),
                "include_older_if_none": True,
                "time": {
                    "enabled": True,
                    "field": schema_small.get("preferred_time_field") or None,
                    "from": t_from or self.tools.ctx.settings.default_time_from,
                    "to": t_to or self.tools.ctx.settings.default_time_to,
                },
                "filters": [],
                "text_query": indicator["value"],
                "group_by": None,
                "metric": {"fn": "count", "field": None},
            }

        planner_messages = [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": json.dumps({"question": user_text, "schema": schema_small}, ensure_ascii=False)}
        ]
        resp = await self.llm.chat(messages=_trim_context(planner_messages, max_chars=90_000))
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        plan_obj = _extract_json_object(msg.get("content") or "")

        if not isinstance(plan_obj, dict):
            plan_obj = {
                "pattern": pattern,
                "mode": "search",
                "limit": min(20, self.tools.ctx.settings.row_limit),
                "include_older_if_none": True,
                "time": {
                    "enabled": True,
                    "field": schema_small.get("preferred_time_field") or None,
                    "from": t_from or self.tools.ctx.settings.default_time_from,
                    "to": t_to or self.tools.ctx.settings.default_time_to,
                },
                "filters": [],
                "text_query": user_text,
                "group_by": None,
                "metric": {"fn": "count", "field": None},
            }

        plan_obj["pattern"] = pattern
        plan_obj.setdefault("mode", "search")
        plan_obj.setdefault("filters", [])
        plan_obj.setdefault("include_older_if_none", True)
        plan_obj.setdefault("limit", min(20, self.tools.ctx.settings.row_limit))
        plan_obj["limit"] = max(1, min(int(plan_obj["limit"]), self.tools.ctx.settings.row_limit))

        if "time" not in plan_obj or not isinstance(plan_obj["time"], dict):
            plan_obj["time"] = {
                "enabled": True,
                "field": schema_small.get("preferred_time_field"),
                "from": self.tools.ctx.settings.default_time_from,
                "to": self.tools.ctx.settings.default_time_to,
            }
        else:
            plan_obj["time"].setdefault("enabled", True)
            plan_obj["time"].setdefault("field", schema_small.get("preferred_time_field"))
            plan_obj["time"].setdefault("from", self.tools.ctx.settings.default_time_from)
            plan_obj["time"].setdefault("to", self.tools.ctx.settings.default_time_to)

        tt_from, tt_to = _extract_time_window(user_text)
        if tt_from:
            plan_obj["time"]["from"] = tt_from
        if tt_to:
            plan_obj["time"]["to"] = tt_to

        plan_obj.setdefault("group_by", None)
        plan_obj.setdefault("metric", {"fn": "count", "field": None})
        return plan_obj

    # -------------------------
    # DSL: terms aggregation (group-by terms)
    # -------------------------
    async def _execute_terms_agg(self, pattern: str, plan: Dict[str, Any], schema_small: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        fields_map, date_fields = await self.tools.ctx.schema_cache.get_flat_fields(pattern)

        time_cfg = plan.get("time") or {}
        time_from = time_cfg.get("from") or self.tools.ctx.settings.default_time_from
        time_to = time_cfg.get("to") or self.tools.ctx.settings.default_time_to
        time_enabled = bool(time_cfg.get("enabled", True))

        time_field = schema_small.get("preferred_time_field")
        if time_enabled:
            time_field = time_field or await _pick_time_field_by_probe(self, pattern, date_fields, time_from, time_to)

        raw_q = str(plan.get("raw_question") or "")

        group_by = str(plan.get("group_by") or "").strip()
        if not group_by:
            # improved fallback instead of defaulting to threat actors always
            ent = _detect_top_entity(raw_q) or _infer_topic(raw_q)
            if ent == "cves":
                group_by = "entities.cves"
            elif ent == "ips":
                group_by = "entities.ips"
            elif ent == "domains":
                group_by = "entities.domains"
            elif ent == "hashes":
                group_by = "entities.hashes"
            elif ent == "actors":
                group_by = "entities.threat_actors"
            else:
                group_by = "entities.threat_actors"

        agg_field = _resolve_field(group_by, fields_map) or group_by
        if agg_field in fields_map and fields_map.get(agg_field) in TEXT_TYPES:
            kw = agg_field + ".keyword"
            if kw in fields_map:
                agg_field = kw

        if agg_field not in fields_map:
            artifact = {"index": pattern, "stage": "dsl_terms_agg_field_missing", "field_used": agg_field, "count": 0, "hits": []}
            return (
                f"**No results:** cannot aggregate because field `{group_by}` is not present in mappings for `{pattern}`.",
                artifact
            )

        must: List[Dict[str, Any]] = []
        must_not: List[Dict[str, Any]] = []
        filter_list: List[Dict[str, Any]] = []

        if time_enabled and time_field:
            filter_list.append({"range": {time_field: {"gte": time_from, "lte": time_to}}})

        text_query = (plan.get("text_query") or "").strip()
        if text_query:
            msg_fields = schema_small.get("message_fields") or []
            fields_for_text = [f for f in msg_fields if isinstance(f, str)] or ["*"]
            must.append({"simple_query_string": {"query": text_query, "fields": fields_for_text, "default_operator": "and"}})

        for f in plan.get("filters") or []:
            if not isinstance(f, dict):
                continue
            clause = _make_clause(str(f.get("field") or ""), str(f.get("op") or "eq"), f.get("value"), fields_map)
            if not clause:
                continue
            if f.get("op") == "neq" and "bool" in clause and "must_not" in clause["bool"]:
                must_not.extend(clause["bool"]["must_not"])
            else:
                must.append(clause)

        bad_vals = ["no significant information detected", "no significant activity detected", "unknown", "n/a", "none", "-", ""]
        if any(x in agg_field.lower() for x in ["actor", "adversary", "threat", "group"]):
            must_not.append({"terms": {agg_field: bad_vals}})

        top_n_q = _extract_top_n(raw_q, default=5)
        # prefer explicit "top N" in question; else fallback to plan.limit
        top_n = top_n_q if ("top " in raw_q.lower() or "most " in raw_q.lower()) else int(plan.get("limit") or top_n_q)
        top_n = max(1, min(top_n, 50))

        metric = plan.get("metric") or {"fn": "count", "field": None}
        metric_fn = str((metric.get("fn") or "count")).lower()
        metric_field = str(metric.get("field") or "").strip()
        metric_field_res = _resolve_field(metric_field, fields_map) if metric_field else None

        aggs: Dict[str, Any] = {
            "top_terms": {
                "terms": {"field": agg_field, "size": top_n, "order": {"_count": "desc"}}
            }
        }

        if metric_fn in ("avg", "sum", "min", "max") and metric_field_res:
            aggs["top_terms"]["aggs"] = {"metric": {metric_fn: {"field": metric_field_res}}}

        body = {
            "size": 0,
            "query": {"bool": {"filter": filter_list, "must": must, "must_not": must_not}},
            "aggs": aggs,
            "track_total_hits": False
        }

        res = await self.tools.run("dsl_search", {"index": pattern, "body": body, "size": 0})
        aggs_res = (res or {}).get("aggregations") or {}
        buckets = ((aggs_res.get("top_terms") or {}).get("buckets") or [])

        pseudo_hits = []
        for b in buckets:
            src = {"key": b.get("key"), "mentions": b.get("doc_count")}
            if "metric" in b:
                src["metric"] = b["metric"].get("value")
            pseudo_hits.append({"_index": pattern, "_id": str(b.get("key")), "_score": b.get("doc_count"), "_source": src})

        artifact = {
            "index": pattern,
            "dsl": body,
            "stage": "dsl_terms_agg",
            "time_field_used": time_field,
            "field_used": agg_field,
            "buckets": buckets,
            "count": len(pseudo_hits),
            "hits": pseudo_hits,
            "aggregations": aggs_res,
        }

        lines = [f"### Top {top_n} values for `{agg_field}`"]
        for i, b in enumerate(buckets[:top_n], 1):
            extra = ""
            if metric_fn in ("avg", "sum", "min", "max") and isinstance(b.get("metric"), dict):
                extra = f" (metric: {b['metric'].get('value')})"
            lines.append(f"{i}. **{b.get('key')}** — {b.get('doc_count')} docs{extra}")

        lines.append("")
        lines.append("**Filters used**")
        lines.append(f"- Engine: `DSL`")
        lines.append(f"- Index pattern: `{pattern}`")
        lines.append(f"- Time field: `{time_field}`")
        lines.append(f"- Time range: `{time_from}` → `{time_to}`")
        lines.append(f"- Group field: `{agg_field}`")
        return "\n".join(lines), artifact

    # -------------------------
    # DSL: fallback ladder search
    # -------------------------
    async def _execute_with_fallback(self, pattern: str, plan: Dict[str, Any], schema_small: Dict[str, Any]) -> Dict[str, Any]:
        fields_map, date_fields = await self.tools.ctx.schema_cache.get_flat_fields(pattern)

        time_cfg = plan.get("time") or {}
        time_enabled = bool(time_cfg.get("enabled", True))
        time_from = time_cfg.get("from") or self.tools.ctx.settings.default_time_from
        time_to = time_cfg.get("to") or self.tools.ctx.settings.default_time_to

        time_field = schema_small.get("preferred_time_field")
        if time_enabled:
            time_field = time_field or await _pick_time_field_by_probe(self, pattern, date_fields, time_from, time_to)

        msg_fields = schema_small.get("message_fields") or []
        if not msg_fields:
            msg_fields = [f for f, t in fields_map.items() if t in TEXT_TYPES][:25]

        limit = int(plan.get("limit") or 20)
        limit = max(1, min(limit, self.tools.ctx.settings.row_limit))
        include_older = bool(plan.get("include_older_if_none", True))
        text_query = (plan.get("text_query") or "").strip()

        must: List[Dict[str, Any]] = []
        must_not: List[Dict[str, Any]] = []
        filters_list = plan.get("filters") or []

        for f in filters_list:
            if not isinstance(f, dict):
                continue
            clause = _make_clause(str(f.get("field") or ""), str(f.get("op") or "eq"), f.get("value"), fields_map)
            if not clause:
                continue
            if f.get("op") == "neq" and "bool" in clause and "must_not" in clause["bool"]:
                must_not.extend(clause["bool"]["must_not"])
            else:
                must.append(clause)

        filter_list: List[Dict[str, Any]] = []
        if time_enabled and time_field:
            filter_list.append({"range": {time_field: {"gte": time_from, "lte": time_to}}})

        src_fields = list(dict.fromkeys(
            ([time_field] if time_field else [])
            + (schema_small.get("cve_fields") or [])[:12]
            + msg_fields[:10]
            + ["title", "description", "summary", "message"]
        ))
        src_fields = [f for f in src_fields if isinstance(f, str) and f]

        # Stage 1
        stage1_should = []
        if text_query:
            stage1_should.append({
                "simple_query_string": {
                    "query": text_query,
                    "fields": msg_fields[:40] if msg_fields else ["*"],
                    "default_operator": "and"
                }
            })

        q1 = {"bool": {"must": must + stage1_should, "filter": filter_list, "must_not": must_not}}
        body1 = {"query": q1, "size": limit, "track_total_hits": False, "_source": src_fields}
        r1 = await self.tools.run("dsl_search", {"index": pattern, "body": body1, "size": limit})
        if isinstance(r1, dict) and r1.get("count", 0) > 0:
            return {**r1, "stage": "stage1", "time_field_used": time_field}

        # Stage 2
        if text_query:
            q2 = {"bool": {"must": must, "filter": filter_list, "must_not": must_not, "should": [
                {"multi_match": {"query": text_query, "fields": msg_fields[:50] if msg_fields else ["*"], "type": "best_fields", "operator": "and"}}
            ], "minimum_should_match": 1}}
            body2 = {"query": q2, "size": limit, "track_total_hits": False, "_source": src_fields}
            r2 = await self.tools.run("dsl_search", {"index": pattern, "body": body2, "size": limit})
            if isinstance(r2, dict) and r2.get("count", 0) > 0:
                return {**r2, "stage": "stage2", "time_field_used": time_field}

        # Stage 3
        if text_query:
            q3 = {"bool": {"filter": filter_list, "must": [
                {"simple_query_string": {"query": text_query, "fields": ["*"], "default_operator": "and"}}
            ]}}
            body3 = {"query": q3, "size": limit, "track_total_hits": False, "_source": src_fields}
            r3 = await self.tools.run("dsl_search", {"index": pattern, "body": body3, "size": limit})
            if isinstance(r3, dict) and r3.get("count", 0) > 0:
                return {**r3, "stage": "stage3", "time_field_used": time_field}

            if include_older and filter_list:
                q4 = {"bool": {"must": [
                    {"simple_query_string": {"query": text_query, "fields": ["*"], "default_operator": "and"}}
                ]}}
                body4 = {"query": q4, "size": limit, "track_total_hits": False, "_source": src_fields}
                r4 = await self.tools.run("dsl_search", {"index": pattern, "body": body4, "size": limit})
                if isinstance(r4, dict) and r4.get("count", 0) > 0:
                    return {**r4, "stage": "stage4_no_time", "time_field_used": time_field}

        return {"index": pattern, "dsl": body1, "count": 0, "hits": [], "stage": "none", "time_field_used": time_field}

    # -------------------------
    # ESQL analytics
    # -------------------------
    async def _run_esql_analytics(self, user_text: str, pattern: str, schema_small: Dict[str, Any], trace: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        t_from, t_to = _extract_time_window(user_text)
        time_from = t_from or self.tools.ctx.settings.default_time_from
        time_to = t_to or self.tools.ctx.settings.default_time_to

        fields_map, date_fields = await self.tools.ctx.schema_cache.get_flat_fields(pattern)
        time_field = schema_small.get("preferred_time_field")
        if time_field and fields_map.get(time_field) not in ("date", "date_nanos"):
            time_field = None
        time_field = time_field or await _pick_time_field_by_probe(self, pattern, date_fields, time_from, time_to)

        interval = _extract_esql_interval(user_text)

        extra_filter = None
        ind = _detect_indicator(user_text)
        if ind:
            extra_filter = {"simple_query_string": {"query": ind["value"], "fields": ["*"], "default_operator": "and"}}
        else:
            qm = re.search(r"\"([^\"]{3,200})\"", user_text)
            if qm:
                extra_filter = {"simple_query_string": {"query": f"\"{qm.group(1)}\"", "fields": ["*"], "default_operator": "and"}}

        field_lines = []
        for f in (schema_small.get("sample_fields") or [])[:180]:
            if isinstance(f, dict) and f.get("field") and f.get("type"):
                field_lines.append(f"{f['field']}:{f['type']}")
        if schema_small.get("message_fields"):
            field_lines.append("message_fields=" + ",".join(schema_small["message_fields"][:12]))

        row_limit = int(self.tools.ctx.settings.row_limit)

        writer_messages = [
            {"role": "system", "content": ESQL_WRITER_PROMPT.format(pattern=pattern, time_field=time_field, row_limit=row_limit)},
            {"role": "user", "content": json.dumps({
                "question": user_text,
                "index_pattern": pattern,
                "time_field": time_field,
                "requested_interval": interval,
                "available_fields": field_lines[:220],
                "examples": [
                    f"FROM {pattern} | STATS events = COUNT(*) | LIMIT 1",
                    f"FROM {pattern} | EVAL bucket = DATE_TRUNC(1 day, {time_field}) | STATS events = COUNT(*) BY bucket | SORT bucket | LIMIT {min(200, row_limit)}"
                ]
            }, ensure_ascii=False)}
        ]

        resp = await self.llm.chat(messages=_trim_context(writer_messages, max_chars=90_000))
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        esql = _extract_esql_from_llm(msg.get("content") or "")

        if not esql.lower().startswith("from "):
            esql = f"FROM {pattern}\n| LIMIT {min(20, row_limit)}"

        for attempt in range(3):
            res = await self.tools.run("esql_query", {
                "query": esql,
                "time_field": time_field,
                "time_from": time_from,
                "time_to": time_to,
                "filter": extra_filter,
                "limit": min(200, row_limit),
            })

            if isinstance(res, dict) and res.get("error"):
                trace.append({"step": "esql_exec_error", "attempt": attempt + 1, "error": res.get("error")})
                repair_messages = [
                    {"role": "system", "content": ESQL_REPAIR_PROMPT.format(pattern=pattern, row_limit=row_limit)},
                    {"role": "user", "content": json.dumps({
                        "question": user_text,
                        "time_field": time_field,
                        "requested_interval": interval,
                        "available_fields": field_lines[:220],
                        "previous_esql": esql,
                        "error": res.get("error"),
                    }, ensure_ascii=False)}
                ]
                rr = await self.llm.chat(messages=_trim_context(repair_messages, max_chars=90_000))
                rmsg = (rr.get("choices") or [{}])[0].get("message") or {}
                esql = _extract_esql_from_llm(rmsg.get("content") or "")
                if not esql.lower().startswith("from "):
                    esql = f"FROM {pattern}\n| LIMIT {min(20, row_limit)}"
                continue

            artifact = {**res}
            artifact["engine"] = "esql"
            artifact["time_from"] = time_from
            artifact["time_to"] = time_to
            artifact["extra_filter"] = extra_filter

            trace.append({"step": "route", "engine": "esql"})
            trace.append({"step": "esql_exec_ok", "time_field_used": res.get("time_field_used"), "rows": res.get("row_count")})

            evidence = _sanitize_tool_result("esql_query", res)
            summary_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "assistant", "content": "EXECUTION_RESULT_ESQL_JSON (evidence):\n" + _clip_text(json.dumps(evidence, ensure_ascii=False), MAX_TOOL_MESSAGE_CHARS)},
                {"role": "system", "content": (
                    "Write a clear answer. Use bullets. Mention engine=ES|QL, time field, time range, and any filters. "
                    "If the table is a time series, describe the trend and highlight peaks."
                )}
            ]
            sr = await self.llm.chat(messages=_trim_context(summary_messages, max_chars=90_000))
            smsg = (sr.get("choices") or [{}])[0].get("message") or {}
            answer = smsg.get("content") or "(No answer)"
            return answer, artifact

        trace.append({"step": "route", "engine": "dsl_fallback_after_esql_fail"})
        return "", {"error": "ES|QL failed after retries"}

    # -------------------------
    # Top-N entities (DSL terms agg on structured fields)
    # -------------------------
    async def _execute_top_terms(self, pattern: str, schema_small: Dict[str, Any], time_from: str, time_to: str, top_n: int, candidate_fields: List[str], label: str) -> Tuple[Optional[str], Dict[str, Any]]:
        fields_map, date_fields = await self.tools.ctx.schema_cache.get_flat_fields(pattern)

        time_field = schema_small.get("preferred_time_field")
        if time_field and fields_map.get(time_field) not in ("date", "date_nanos"):
            time_field = None
        time_field = time_field or await _pick_time_field_by_probe(self, pattern, date_fields, time_from, time_to)

        def pick_agg_field(cands: List[str]) -> Optional[str]:
            for f in cands:
                rf = _resolve_field(f, fields_map) or f
                if rf in fields_map and fields_map.get(rf) == "keyword":
                    return rf
                if rf in fields_map and fields_map.get(rf) in TEXT_TYPES:
                    kw = rf + ".keyword"
                    if kw in fields_map and fields_map.get(kw) == "keyword":
                        return kw
            return None

        agg_field = pick_agg_field(candidate_fields)
        if not agg_field:
            artifact = {
                "index": pattern,
                "stage": f"top_{label}_no_structured_field",
                "time_field_used": time_field,
                "time_range": {"from": time_from, "to": time_to},
                "field_used": None,
                "dsl": {"note": "No aggregatable field found for this entity type."},
                "count": 0,
                "hits": [],
            }
            return None, artifact

        bad_vals = ["no significant information detected", "no significant activity detected", "unknown", "n/a", "none", "-", ""]
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"range": {time_field: {"gte": time_from, "lte": time_to}}},
                        {"exists": {"field": agg_field}}
                    ],
                    "must_not": [{"terms": {agg_field: bad_vals}}]
                }
            },
            "aggs": {
                "top_terms": {"terms": {"field": agg_field, "size": max(1, min(top_n, 50)), "order": {"_count": "desc"}}}
            },
            "track_total_hits": False
        }

        res = await self.tools.run("dsl_search", {"index": pattern, "body": body, "size": 0})
        buckets = (((res or {}).get("aggregations") or {}).get("top_terms") or {}).get("buckets") or []

        pseudo_hits = [
            {"_index": pattern, "_id": str(b.get("key")), "_score": b.get("doc_count"), "_source": {"name": b.get("key"), "mentions": b.get("doc_count")}}
            for b in buckets
        ]

        artifact = {
            "index": pattern,
            "dsl": body,
            "stage": f"top_{label}_dsl_terms",
            "time_field_used": time_field,
            "time_range": {"from": time_from, "to": time_to},
            "field_used": agg_field,
            "buckets": buckets,
            "count": len(pseudo_hits),
            "hits": pseudo_hits,
            "aggregations": (res or {}).get("aggregations") or {},
        }

        if not buckets:
            return None, artifact

        lines = [f"### Top {min(top_n, 50)} {label}", ""]
        for i, b in enumerate(buckets[:top_n], 1):
            lines.append(f"{i}. **{b.get('key')}** — {b.get('doc_count')} mentions")

        lines.append("")
        lines.append("**Filters used**")
        lines.append(f"- Engine: `DSL`")
        lines.append(f"- Index pattern: `{pattern}`")
        lines.append(f"- Time field: `{time_field}`")
        lines.append(f"- Time range: `{time_from}` → `{time_to}`")
        lines.append(f"- Aggregated field: `{agg_field}`")

        return "\n".join(lines), artifact

    async def _maybe_handle_top_entities(self, user_text: str, pattern: str, schema_small: Dict[str, Any], trace: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not _is_top_question(user_text):
            return None, None

        entity = _detect_top_entity(user_text)
        if not entity:
            return None, None

        top_n = _extract_top_n(user_text, default=5)
        t_from, t_to = _extract_time_window(user_text)
        time_from = t_from or "now-30d"
        time_to = t_to or "now"

        if entity == "actors":
            label = "threat actors"
        elif entity == "cves":
            label = "CVEs"
        elif entity == "ips":
            label = "IPs"
        elif entity == "domains":
            label = "domains"
        elif entity == "hashes":
            label = "hashes"
        else:
            return None, None

        candidates = _semantic_candidates(schema_small, entity)
        if not candidates:
            return None, {
                "index": pattern,
                "stage": f"top_{label}_no_schema_candidates",
                "time_range": {"from": time_from, "to": time_to},
                "hits": [],
                "count": 0,
            }

        answer, artifact = await self._execute_top_terms(pattern, schema_small, time_from, time_to, top_n, candidates, label)
        trace.append({"step": "top_terms", "entity": entity, "stage": artifact.get("stage"), "field_used": artifact.get("field_used")})
        return answer, artifact

    # -------------------------
    # Main run
    # -------------------------
    async def run(self, conversation_id: str, user_text: str) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
        messages = self.store.get(conversation_id)
        if not messages:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        messages.append({"role": "user", "content": user_text})
        messages = _trim_context(messages)

        trace: List[Dict[str, Any]] = []

        # Update topic/time state for follow-ups
        topic = _infer_topic(user_text)
        if topic:
            self._state_update(conversation_id, last_topic=topic)
        tf, tt = _extract_time_window(user_text)
        if tf:
            self._state_update(conversation_id, last_time_from=tf, last_time_to=tt or "now")

        # 1) schema-first
        messages, schema_small, pattern, tr = await self._schema_first(messages)
        trace.extend(tr)

        # 2) COUNT handler (fixes your wrong “top terms” output for count questions)
        count_answer, count_artifact = await self._handle_count_request(conversation_id, user_text, pattern, schema_small, trace)
        if count_answer and count_artifact is not None:
            messages.append({"role": "assistant", "content": count_answer})
            messages = _trim_context(messages)
            self.store.set(conversation_id, messages)
            return count_answer, count_artifact, trace

        # 3) deterministic top-N entities (DSL aggregations)
        top_answer, top_artifact = await self._maybe_handle_top_entities(user_text, pattern, schema_small, trace)
        if top_answer and top_artifact is not None:
            # store state for follow-up counts
            self._state_update(conversation_id, last_top_entity=_detect_top_entity(user_text))
            messages.append({"role": "assistant", "content": top_answer})
            messages = _trim_context(messages)
            self.store.set(conversation_id, messages)
            return top_answer, top_artifact, trace

        # 4) Router: ES|QL analytics vs DSL
        if _should_use_esql(user_text):
            answer, artifact = await self._run_esql_analytics(user_text, pattern, schema_small, trace)
            if answer:
                messages.append({"role": "assistant", "content": answer})
                messages = _trim_context(messages)
                self.store.set(conversation_id, messages)
                return answer, artifact, trace

        trace.append({"step": "route", "engine": "dsl"})

        # 5) DSL planner
        plan = await self._plan(user_text, schema_small, pattern)
        trace.append({"step": "plan", "plan": _clip_any(plan)})

        # 6) DSL aggregate mode
        if str(plan.get("mode") or "").lower() == "aggregate":
            agg_answer, agg_artifact = await self._execute_terms_agg(pattern, {**plan, "raw_question": user_text}, schema_small)
            messages.append({"role": "assistant", "content": agg_answer})
            messages = _trim_context(messages)
            self.store.set(conversation_id, messages)
            trace.append({"step": "execute", "stage": agg_artifact.get("stage"), "engine": "dsl"})
            return agg_answer, agg_artifact, trace

        # 7) DSL search mode with fallback ladder
        artifact = await self._execute_with_fallback(pattern, plan, schema_small)
        trace.append({"step": "execute", "stage": artifact.get("stage"), "time_field_used": artifact.get("time_field_used"), "engine": "dsl"})

        artifact_small = _sanitize_tool_result("dsl_search", artifact)
        messages.append({
            "role": "assistant",
            "content": "EXECUTION_RESULT_DSL_JSON (evidence):\n" + _clip_text(json.dumps(artifact_small, ensure_ascii=False), MAX_TOOL_MESSAGE_CHARS)
        })
        messages = _trim_context(messages)

        final_messages = _trim_context(messages + [{
            "role": "system",
            "content": (
                "Write a clear professional answer using ONLY the evidence. "
                "Use bullets. Mention engine=DSL, time field, time range, index pattern, and stage. "
                "If count=0 say so and mention fallback stage used."
            )
        }])

        try:
            resp = await self.llm.chat(messages=final_messages)
        except LLMError as e:
            if "context_length_exceeded" in str(e):
                resp = await self.llm.chat(messages=_trim_context(final_messages, max_chars=80_000))
            else:
                raise

        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        answer = msg.get("content") or "(No answer)"

        messages.append({"role": "assistant", "content": answer})
        messages = _trim_context(messages)

        self.store.set(conversation_id, messages)
        return answer, artifact, trace
