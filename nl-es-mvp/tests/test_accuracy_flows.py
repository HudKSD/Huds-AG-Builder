import unittest
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


if __name__ == "__main__":
    unittest.main()
