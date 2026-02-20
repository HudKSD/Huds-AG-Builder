import unittest
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import NL2ESAgent


class DummyLLM:
    async def chat(self, *args, **kwargs):
        return {"choices": [{"message": {"content": "ok"}}]}


class DummyStore:
    def __init__(self):
        self._d = {}

    def get(self, cid):
        return self._d.get(cid, [])

    def set(self, cid, msgs):
        self._d[cid] = msgs


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


if __name__ == "__main__":
    unittest.main()
