import os
import logging
from typing import Dict, Any, List

LOG = logging.getLogger("enrichment.bootstrap")

TEMPLATE_NAME = "data_cached_entities_template"

def _entities_mapping_properties() -> Dict[str, Any]:
    # Add both ECS-like and simple entities fields (safe + flexible)
    return {
        "threat": {
            "properties": {
                "group": {
                    "properties": {
                        "name": {"type": "keyword"},
                        "alias": {"type": "keyword"},
                    }
                }
            }
        },
        "entities": {
            "properties": {
                "threat_actors": {"type": "keyword"},
                "cves": {"type": "keyword"},
                "ips": {"type": "keyword"},
                "domains": {"type": "keyword"},
                "hashes": {"type": "keyword"},
            }
        },
        "enrichment": {
            "properties": {
                "version": {"type": "keyword"},
                "processed_at": {"type": "date"},
                "source_fields": {"type": "keyword"},
            }
        }
    }

async def ensure_index_template(es, index_pattern: str) -> None:
    body = {
        "index_patterns": [index_pattern],
        "priority": 500,
        "template": {
            "mappings": {"properties": _entities_mapping_properties()}
        },
        "_meta": {"managed_by": "nl-es-mvp-enricher"}
    }

    try:
        await es.transport.perform_request("PUT", f"/_index_template/{TEMPLATE_NAME}", body=body)
        LOG.info("Ensured index template: %s", TEMPLATE_NAME)
    except Exception as e:
        LOG.error("Failed to create/update index template: %s", e)

async def ensure_mapping_on_existing_indices(es, index_pattern: str) -> List[str]:
    # list indices
    indices = []
    try:
        rows = await es.cat.indices(index=index_pattern, format="json")
        indices = [r.get("index") for r in rows if r.get("index")]
    except Exception:
        try:
            resp = await es.indices.get(index=index_pattern, allow_no_indices=True)
            indices = list((resp or {}).keys())
        except Exception:
            indices = []

    props = _entities_mapping_properties()
    updated = []

    for idx in indices:
        try:
            await es.transport.perform_request("PUT", f"/{idx}/_mapping", body={"properties": props})
            updated.append(idx)
        except Exception as e:
            LOG.warning("Mapping update skipped/failed for %s: %s", idx, e)

    if updated:
        LOG.info("Ensured mappings for %d indices", len(updated))
    else:
        LOG.warning("No indices updated (none found or all failed)")
    return updated

async def ensure_actor_dictionary_index(es, dict_index: str) -> None:
    body = {
        "mappings": {
            "properties": {
                "canonical": {"type": "keyword"},
                "aliases": {"type": "keyword"},
                "category": {"type": "keyword"},  # apt/ransomware/etc (optional)
            }
        }
    }

    try:
        exists = await es.indices.exists(index=dict_index)
        if not exists:
            await es.transport.perform_request("PUT", f"/{dict_index}", body=body)
            LOG.info("Created actor dictionary index: %s", dict_index)

            # Seed with a small default list (optional; you can replace later)
            seed = [
                {"canonical": "Lazarus Group", "aliases": ["Lazarus", "Hidden Cobra"], "category": "apt"},
                {"canonical": "APT28", "aliases": ["Fancy Bear", "Sofacy"], "category": "apt"},
                {"canonical": "APT29", "aliases": ["Cozy Bear"], "category": "apt"},
                {"canonical": "LockBit", "aliases": ["LockBit 3.0", "LockBit Black"], "category": "ransomware"},
                {"canonical": "ALPHV", "aliases": ["BlackCat"], "category": "ransomware"},
            ]
            bulk = []
            for i, doc in enumerate(seed, 1):
                bulk.append({"index": {"_index": dict_index, "_id": str(i)}})
                bulk.append(doc)
            await es.bulk(operations=bulk, refresh="wait_for")
            LOG.info("Seeded %d actor dictionary entries", len(seed))
        else:
            LOG.info("Actor dictionary index exists: %s", dict_index)
    except Exception as e:
        LOG.error("Actor dictionary bootstrap failed: %s", e)

async def bootstrap_all(es, index_pattern: str, dict_index: str) -> None:
    await ensure_index_template(es, index_pattern)
    await ensure_mapping_on_existing_indices(es, index_pattern)
    await ensure_actor_dictionary_index(es, dict_index)

