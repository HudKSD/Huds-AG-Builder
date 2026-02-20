import unittest
import json
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import NL2ESAgent, _trim_context, _sanitize_plan_fields


class DummyLLM:
    async def chat(self, *args, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}


class DummyStore:
    def __init__(self):
        self._d = {}
        self._m = {}
        self.max_turns = 30

    def get(self, cid):
        return self._d.get(cid, [])

    def set(self, cid, msgs):
        self._d[cid] = msgs

    def get_meta(self, cid):
        return dict(self._m.get(cid, {}))

    def update_meta(self, cid, **kwargs):
        m = self._m.get(cid, {})
        m.update(kwargs)
        self._m[cid] = m


class DummyTools:
    def __init__(self):
        self.ctx = SimpleNamespace(settings=SimpleNamespace(es_allowed_patterns=["logs-*", "sec-*"], default_time_from="now-24h", default_time_to="now", row_limit=50))

    async def run(self, name, args):
        if name == "list_indices":
            if args["pattern"] == "logs-*":
                return {
                    "items": [
                        {"index": "logs-a", "docs_count": 10},
                        {"index": "shared", "docs_count": 3},
                    ],
                    "count": 2,
                    "total_docs": 13,
                }
            return {
                "items": [
                    {"index": "sec-a", "docs_count": 6},
                    {"index": "shared", "docs_count": 5},
                ],
                "count": 2,
                "total_docs": 11,
            }
        if name == "count_documents":
            return {"count": 42}
        if name == "terms_aggregate":
            return {"items": [{"key": "x", "doc_count": 9}], "count": 1, "aggregations": {}}
        if name == "capability_preflight":
            return {"pattern": args.get("pattern"), "indices_count": 2, "total_docs": 16, "time_fields": ["@timestamp"], "aggregatable_fields": ["field.keyword"], "searchable_fields": ["message"], "field_count": 4}
        return {}


class AccuracyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.agent = NL2ESAgent(llm=DummyLLM(), tools=DummyTools(), store=DummyStore(), max_steps=4)

    async def test_index_inventory_aggregates_dedup_and_top_n(self):
        trace = []
        answer, artifact = await self.agent._handle_index_inventory_request("c1", "list top 5 indices", trace)
        self.assertIn("Total indices", answer)
        self.assertEqual(artifact["count"], 3)
        self.assertEqual(artifact["total_docs"], 21)
        names = [x["index"] for x in artifact["items"]]
        self.assertEqual(names[0], "logs-a")

    async def test_count_docs_uses_tool(self):
        c = await self.agent._count_docs("logs-*", {"match_all": {}})
        self.assertEqual(c, 42)

    async def test_verify_detects_missing_counts(self):
        answer, warns = self.agent._verify_answer_against_artifact("No numbers here", {"count": 7})
        self.assertTrue(warns)
        self.assertIn("Verification notes", answer)

    async def test_trim_context_respects_token_budget(self):
        messages = [
            {"role": "system", "content": "s" * 200},
            {"role": "user", "content": "word " * 4000},
            {"role": "assistant", "content": "word " * 4000},
            {"role": "user", "content": "latest"},
        ]
        trimmed = _trim_context(messages, max_chars=200_000, max_tokens=250)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[-1]["content"], "latest")
        self.assertLess(len(trimmed), len(messages))

    async def test_sanitize_plan_fields_drops_unknown_fields(self):
        schema_small = {
            "sample_fields": [{"field": "message"}, {"field": "host.name"}],
            "date_fields": ["@timestamp"],
            "preferred_time_field": "@timestamp",
        }
        plan = {
            "pattern": "logs-*",
            "mode": "aggregate",
            "limit": 10,
            "include_older_if_none": True,
            "time": {"enabled": True, "field": "bad.time", "from": "now-1d", "to": "now"},
            "filters": [
                {"field": "message", "op": "match", "value": "error"},
                {"field": "unknown.field", "op": "eq", "value": "x"},
            ],
            "group_by": "unknown.field",
            "metric": {"fn": "count_distinct", "field": "unknown.field"},
        }
        out = _sanitize_plan_fields(plan, schema_small)
        self.assertEqual(out["time"]["field"], "@timestamp")
        self.assertEqual(len(out["filters"]), 1)
        self.assertIsNone(out["group_by"])
        self.assertIsNone(out["metric"]["field"])

    async def test_compaction_builds_and_injects_memory_summary(self):
        cid = "comp1"
        msgs = [{"role": "system", "content": "base"}]
        for i in range(30):
            msgs.append({"role": "user", "content": ("user message " + str(i) + " ") * 150})
            msgs.append({"role": "assistant", "content": ("assistant reply " + str(i) + " ") * 150})

        compacted = self.agent._maybe_compact_messages(cid, msgs)
        self.assertLess(len(compacted), len(msgs))
        self.assertTrue(any((m.get("role") == "system" and "[Conversation memory]" in str(m.get("content") or "")) for m in compacted))

        meta = self.agent.store.get_meta(cid)
        self.assertTrue(meta.get("compacted"))
        self.assertTrue(isinstance(meta.get("rolling_summary"), str) and len(meta.get("rolling_summary")) > 0)


class WorkflowLLM:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})

        if tools:
            return {
                "choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "schema0",
                            "type": "function",
                            "function": {
                                "name": "get_mappings",
                                "arguments": json.dumps({"pattern": "logs-*", "max_fields": 400}),
                            }
                        }]
                    }
                }]
            }

        system_text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        assistant_msgs = [str(m.get("content") or "") for m in messages if m.get("role") == "assistant"]
        assistant_text = "\n".join(assistant_msgs)
        latest_assistant = assistant_msgs[-1] if assistant_msgs else ""

        if "STRICT Elasticsearch query planner" in system_text:
            question = ""
            for m in messages:
                if m.get("role") == "user":
                    try:
                        obj = json.loads(m.get("content") or "{}")
                        question = str(obj.get("question") or "")
                    except Exception:
                        question = str(m.get("content") or "")
            tq = "malware"
            if "unc1069" in question.lower():
                tq = "UNC1069"
            plan = {
                "pattern": "logs-*",
                "mode": "search",
                "limit": 10,
                "include_older_if_none": True,
                "time": {"enabled": True, "field": "@timestamp", "from": "now-7d", "to": "now"},
                "filters": [],
                "text_query": tq,
                "group_by": None,
                "metric": {"fn": "count", "field": None},
            }
            return {"choices": [{"message": {"content": json.dumps(plan)}}]}

        if "ES|QL query writer" in system_text:
            return {"choices": [{"message": {"content": "FROM logs-* | STATS events = COUNT(*) | LIMIT 10"}}]}

        if "Write a clear answer. Use bullets. Mention engine=ES|QL" in system_text:
            return {"choices": [{"message": {"content": "- Engine: ES|QL\n- Trend is stable in mock data."}}]}

        if "Write a clear professional answer using ONLY the evidence" in system_text and "EXECUTION_RESULT_DSL_JSON" in latest_assistant:
            if "UNC1069" in latest_assistant and '"stage": "none"' in latest_assistant:
                return {"choices": [{"message": {"content": "No information on UNC1069 was found in the selected time range."}}]}
            return {"choices": [{"message": {"content": "- Engine: DSL\n- Found 1 matching document in mock data."}}]}

        return {"choices": [{"message": {"content": "ok"}}]}


class WorkflowSchemaCache:
    async def get_flat_fields(self, pattern):
        return ({
            "@timestamp": "date",
            "message": "text",
            "message.keyword": "keyword",
            "host.name": "keyword",
            "entities.cves": "keyword",
            "entities.cves.keyword": "keyword",
        }, ["@timestamp"])


class WorkflowES:
    async def count(self, index=None, query=None):
        return {"count": 5}


class WorkflowTools:
    def __init__(self):
        settings = SimpleNamespace(
            es_allowed_patterns=["logs-*"],
            default_time_from="now-24h",
            default_time_to="now",
            row_limit=50,
        )
        self.ctx = SimpleNamespace(settings=settings, schema_cache=WorkflowSchemaCache(), es=WorkflowES())

    def schemas(self):
        return [{"type": "function", "function": {"name": "get_mappings", "parameters": {"type": "object", "properties": {}}}}]

    async def run(self, name, args):
        if name == "get_mappings":
            return {
                "pattern": args.get("pattern", "logs-*"),
                "field_count": 6,
                "preferred_time_field": "@timestamp",
                "date_fields": ["@timestamp"],
                "cve_fields": ["entities.cves.keyword"],
                "message_fields": ["message"],
                "sample_fields": [
                    {"field": "@timestamp", "type": "date", "aggregatable": True, "searchable": True},
                    {"field": "message", "type": "text", "aggregatable": False, "searchable": True},
                    {"field": "entities.cves.keyword", "type": "keyword", "aggregatable": True, "searchable": True},
                ],
                "field_caps": {},
            }
        if name == "capability_preflight":
            return {
                "pattern": args.get("pattern", "logs-*"),
                "indices_count": 1,
                "total_docs": 100,
                "preferred_time_field": "@timestamp",
                "time_fields": ["@timestamp"],
                "aggregatable_fields": ["entities.cves.keyword"],
                "searchable_fields": ["message"],
                "field_count": 6,
            }
        if name == "count_documents":
            return {"count": 42}
        if name == "dsl_search":
            body = args.get("body", {}) or {}
            qtxt = json.dumps(body).lower()
            if "unc1069" in qtxt:
                if "now-365d" in qtxt:
                    return {
                        "index": args.get("index", "logs-*"),
                        "dsl": body,
                        "count": 1,
                        "hits": [{"_index": "logs-1", "_id": "unc-1", "_score": 1.0, "_source": {"message": "UNC1069 activity", "@timestamp": "2025-11-01T00:00:00Z", "entities.cves": ["CVE-2025-1111"]}}],
                        "aggregations": {},
                    }
                return {
                    "index": args.get("index", "logs-*"),
                    "dsl": body,
                    "count": 0,
                    "hits": [],
                    "aggregations": {},
                }
            return {
                "index": args.get("index", "logs-*"),
                "dsl": body,
                "count": 1,
                "hits": [{"_index": "logs-1", "_id": "1", "_score": 1.0, "_source": {"message": "mock malware event", "@timestamp": "2026-01-01T00:00:00Z"}}],
                "aggregations": {},
            }
        if name == "esql_query":
            return {
                "query": args.get("query"),
                "columns": ["events"],
                "rows": [[10]],
                "row_count": 1,
                "time_field_used": args.get("time_field"),
                "took": 1,
            }
        if name == "list_indices":
            return {"indices": ["logs-1"], "items": [{"index": "logs-1", "docs_count": 100}], "count": 1, "total_docs": 100}
        if name == "terms_aggregate":
            return {"items": [], "count": 0, "aggregations": {}}
        return {}


class WorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.llm = WorkflowLLM()
        self.store = DummyStore()
        self.agent = NL2ESAgent(llm=self.llm, tools=WorkflowTools(), store=self.store, max_steps=6)

    async def test_run_count_query_end_to_end_mock(self):
        answer, artifact, trace = await self.agent.run("wf-count", "How many documents in the last 7 days?")
        self.assertIn("Documents matched", answer)
        self.assertEqual(artifact.get("stage"), "count_documents")
        self.assertEqual(artifact.get("count"), 42)
        self.assertTrue(any(t.get("step") == "preflight" for t in trace))

    async def test_run_dsl_chat_query_end_to_end_mock(self):
        answer, artifact, trace = await self.agent.run("wf-dsl", "show documents about malware")
        self.assertIn("Engine: DSL", answer)
        self.assertEqual(artifact.get("count"), 1)
        self.assertTrue(str(artifact.get("stage", "")).startswith("stage"))
        self.assertTrue(any(t.get("step") == "plan" for t in trace))

    async def test_run_esql_chat_query_end_to_end_mock(self):
        answer, artifact, trace = await self.agent.run("wf-esql", "trend per day in the last week")
        self.assertIn("Engine: ES|QL", answer)
        self.assertEqual(artifact.get("engine"), "esql")
        self.assertTrue(any(t.get("step") == "route" and t.get("engine") == "esql" for t in trace))

    async def test_run_compaction_injects_memory_into_workflow(self):
        cid = "wf-compact"
        seed = [{"role": "system", "content": "base"}]
        for i in range(50):
            seed.append({"role": "user", "content": ("user long context " + str(i) + " ") * 180})
            seed.append({"role": "assistant", "content": ("assistant long context " + str(i) + " ") * 180})
        self.store.set(cid, seed)

        await self.agent.run(cid, "How many documents in the last 7 days?")

        meta = self.store.get_meta(cid)
        self.assertTrue(meta.get("compacted"))
        self.assertTrue(meta.get("rolling_summary"))
        saw_memory = False
        for c in self.llm.calls:
            for m in c.get("messages", []):
                if m.get("role") == "system" and "[Conversation memory]" in str(m.get("content") or ""):
                    saw_memory = True
                    break
            if saw_memory:
                break
        self.assertTrue(saw_memory)

    async def test_table_followup_uses_last_artifact_rows(self):
        cid = "wf-table"
        _, artifact, _ = await self.agent.run(cid, "show documents about malware")
        self.assertEqual(artifact.get("count"), 1)

        answer2, artifact2, trace2 = await self.agent.run(cid, "please create it in a table form")
        self.assertIn("| Timestamp | Actor | Attack Summary | Related CVEs |", answer2)
        self.assertIn("mock malware event", answer2)
        self.assertEqual(artifact2.get("count"), 1)
        self.assertTrue(any(t.get("step") == "format_followup" for t in trace2))

    async def test_time_only_followup_reuses_previous_query_intent(self):
        cid = "wf-followup"
        answer1, artifact1, _ = await self.agent.run(cid, "give me information on UNC1069")
        self.assertIn("No information on UNC1069", answer1)
        self.assertEqual(artifact1.get("count"), 0)

        answer2, artifact2, _ = await self.agent.run(cid, "check for the past year")
        self.assertEqual(artifact2.get("count"), 1)
        self.assertIn("Engine: DSL", answer2)


if __name__ == "__main__":
    unittest.main()
