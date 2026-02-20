import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.enrichment.bootstrap import ensure_index_template, ensure_mapping_on_existing_indices, ensure_actor_dictionary_index


class _FakeIndices:
    def __init__(self):
        self.template_calls = []
        self.mapping_calls = []
        self.create_calls = []
        self._exists = False

    async def put_index_template(self, name, **kwargs):
        self.template_calls.append((name, kwargs))
        return {"acknowledged": True}

    async def put_mapping(self, index, properties):
        self.mapping_calls.append((index, properties))
        return {"acknowledged": True}

    async def exists(self, index):
        return self._exists

    async def create(self, index, **kwargs):
        self.create_calls.append((index, kwargs))
        return {"acknowledged": True}

    async def get(self, index, allow_no_indices=True):
        return {"data_cached_fallback": {}}


class _FakeCat:
    async def indices(self, index, format="json"):
        return [{"index": "data_cached_a"}, {"index": "data_cached_b"}]


class _FakeES:
    def __init__(self):
        self.indices = _FakeIndices()
        self.cat = _FakeCat()
        self.bulk_calls = []

    async def bulk(self, operations, refresh=False):
        self.bulk_calls.append((operations, refresh))
        return {"errors": False}


def test_ensure_index_template_uses_indices_api():
    es = _FakeES()
    asyncio.run(ensure_index_template(es, "data_cached_*"))
    assert es.indices.template_calls
    assert es.indices.template_calls[0][0] == "data_cached_entities_template"


def test_ensure_mapping_updates_existing_indices():
    es = _FakeES()
    updated = asyncio.run(ensure_mapping_on_existing_indices(es, "data_cached_*"))
    assert updated == ["data_cached_a", "data_cached_b"]
    assert len(es.indices.mapping_calls) == 2


def test_ensure_actor_dictionary_create_and_seed():
    es = _FakeES()
    asyncio.run(ensure_actor_dictionary_index(es, "rbtn_actor_dictionary"))
    assert es.indices.create_calls
    assert es.bulk_calls
