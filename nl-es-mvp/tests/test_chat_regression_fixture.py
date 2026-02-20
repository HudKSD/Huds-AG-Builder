import json
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chat_regression_fixture.json"

import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import NL2ESAgent


class FixtureLLM:
    async def chat(self, messages, tools=None, tool_choice=None):
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
                                "arguments": json.dumps({"pattern": "data_cached_*", "max_fields": 400}),
                            }
                        }]
                    }
                }]
            }

        system_text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        assistant_msgs = [str(m.get("content") or "") for m in messages if m.get("role") == "assistant"]
        latest_assistant = assistant_msgs[-1] if assistant_msgs else ""

        if "STRICT Elasticsearch query planner" in system_text:
            q = ""
            for m in messages:
                if m.get("role") == "user":
                    try:
                        q = str(json.loads(m.get("content") or "{}").get("question") or "")
                    except Exception:
                        q = str(m.get("content") or "")
            tq = "AI-related attacks"
            if "unc1069" in q.lower():
                tq = "UNC1069"
            plan = {
                "pattern": "data_cached_*",
                "mode": "search",
                "limit": 20,
                "include_older_if_none": True,
                "time": {"enabled": True, "field": "@timestamp", "from": "now-7d", "to": "now"},
                "filters": [],
                "text_query": tq,
                "group_by": None,
                "metric": {"fn": "count", "field": None},
            }
            return {"choices": [{"message": {"content": json.dumps(plan)}}]}

        if "Write a clear professional answer using ONLY the evidence" in system_text and "EXECUTION_RESULT_DSL_JSON" in latest_assistant:
            if "UNC1069" in latest_assistant and '"count": 0' in latest_assistant:
                return {"choices": [{"message": {"content": "No information on UNC1069 was found in the available data for the selected time window."}}]}
            return {"choices": [{"message": {"content": "- Engine: DSL\n- Found AI-related attack activity with 2 matching documents."}}]}

        return {"choices": [{"message": {"content": "ok"}}]}


class FixtureStore:
    def __init__(self):
        self.max_turns = 30
        self._d = {}
        self._m = {}

    def get(self, cid):
        return self._d.get(cid, [])

    def set(self, cid, msgs):
        self._d[cid] = msgs

    def get_meta(self, cid):
        return dict(self._m.get(cid, {}))

    def update_meta(self, cid, **kwargs):
        meta = self._m.get(cid, {})
        meta.update(kwargs)
        self._m[cid] = meta


class FixtureSchemaCache:
    async def get_flat_fields(self, pattern):
        return ({
            "@timestamp": "date",
            "message": "text",
            "summary": "text",
            "actor": "keyword",
            "entities.cves": "keyword",
            "entities.cves.keyword": "keyword",
        }, ["@timestamp"])


class FixtureES:
    async def count(self, index=None, query=None):
        return {"count": 2}


class FixtureTools:
    def __init__(self, docs):
        self.docs = docs
        settings = SimpleNamespace(
            es_allowed_patterns=["data_cached_*"],
            default_time_from="now-24h",
            default_time_to="now",
            row_limit=50,
        )
        self.ctx = SimpleNamespace(settings=settings, schema_cache=FixtureSchemaCache(), es=FixtureES())

    def schemas(self):
        return [{"type": "function", "function": {"name": "get_mappings", "parameters": {"type": "object", "properties": {}}}}]

    async def run(self, name, args):
        if name == "get_mappings":
            return {
                "pattern": "data_cached_*",
                "field_count": 6,
                "preferred_time_field": "@timestamp",
                "date_fields": ["@timestamp"],
                "cve_fields": ["entities.cves"],
                "message_fields": ["message", "summary"],
                "sample_fields": [
                    {"field": "@timestamp", "type": "date", "aggregatable": True, "searchable": True},
                    {"field": "message", "type": "text", "aggregatable": False, "searchable": True},
                    {"field": "summary", "type": "text", "aggregatable": False, "searchable": True},
                    {"field": "actor", "type": "keyword", "aggregatable": True, "searchable": True},
                    {"field": "entities.cves", "type": "keyword", "aggregatable": True, "searchable": True},
                ],
                "field_caps": {},
            }
        if name == "capability_preflight":
            return {
                "pattern": "data_cached_*",
                "indices_count": 3,
                "total_docs": len(self.docs),
                "preferred_time_field": "@timestamp",
                "time_fields": ["@timestamp"],
                "aggregatable_fields": ["actor", "entities.cves"],
                "searchable_fields": ["message", "summary"],
                "field_count": 6,
            }
        if name == "dsl_search":
            body = args.get("body", {}) or {}
            qtxt = json.dumps(body).lower()
            if "unc1069" in qtxt:
                if "now-365d" in qtxt:
                    out = [d for d in self.docs if d.get("_id") == "u1"]
                else:
                    out = []
                return {"index": "data_cached_*", "dsl": body, "count": len(out), "hits": out, "aggregations": {}}

            if "ai-related attacks" in qtxt or "ai" in qtxt:
                out = [d for d in self.docs if d.get("_id") in ("a1", "a2")]
                return {"index": "data_cached_*", "dsl": body, "count": len(out), "hits": out, "aggregations": {}}

            out = [self.docs[0]] if self.docs else []
            return {"index": "data_cached_*", "dsl": body, "count": len(out), "hits": out, "aggregations": {}}

        if name == "count_documents":
            return {"count": 2}
        if name == "list_indices":
            return {"indices": ["data_cached_2026.02.14"], "items": [{"index": "data_cached_2026.02.14", "docs_count": 2}], "count": 1, "total_docs": 2}
        if name == "terms_aggregate":
            return {"items": [], "count": 0, "aggregations": {}}
        if name == "esql_query":
            return {"query": args.get("query"), "columns": ["events"], "rows": [[2]], "row_count": 1, "time_field_used": args.get("time_field"), "took": 1}
        return {}


class ChatRegressionFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixture = json.loads(FIXTURE.read_text())
        self.docs = fixture["docs"]
        self.agent = NL2ESAgent(llm=FixtureLLM(), tools=FixtureTools(self.docs), store=FixtureStore(), max_steps=6)

    async def test_ai_attacks_week_then_ad_cves_then_table(self):
        cid = "chat-fixture-1"
        answer1, artifact1, _ = await self.agent.run(cid, "list AI related attacks in the past 7 days")
        self.assertIn("Engine: DSL", answer1)
        self.assertEqual(artifact1.get("count"), 2)

        answer2, artifact2, _ = await self.agent.run(cid, "also list some attacks that target active directory and their related CVEs")
        self.assertEqual(artifact2.get("count"), 2)
        self.assertIn("Engine: DSL", answer2)

        answer3, artifact3, trace3 = await self.agent.run(cid, "please create it in a table form")
        self.assertIn("| Timestamp | Actor | Attack Summary | Related CVEs |", answer3)
        self.assertIn("CVE-2025-53770", answer3)
        self.assertEqual(artifact3.get("count"), 2)
        self.assertTrue(any(t.get("step") == "format_followup" for t in trace3))

    async def test_unc1069_time_followups(self):
        cid = "chat-fixture-2"
        answer1, artifact1, _ = await self.agent.run(cid, "give me information on UNC1069")
        self.assertIn("No information on UNC1069", answer1)
        self.assertEqual(artifact1.get("count"), 0)

        answer2, artifact2, _ = await self.agent.run(cid, "check for the past 7 days")
        self.assertIn("No information on UNC1069", answer2)
        self.assertEqual(artifact2.get("count"), 0)

        answer3, artifact3, _ = await self.agent.run(cid, "check for the past year")
        self.assertEqual(artifact3.get("count"), 1)
        self.assertIn("Engine: DSL", answer3)


if __name__ == "__main__":
    unittest.main()
