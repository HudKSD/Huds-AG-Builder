import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from elasticsearch import AsyncElasticsearch

from ..config import Settings
from ..llm_client import OpenAICompatibleClient
from .bootstrap import bootstrap_all
from .extractors import ActorDictionary, collect_text, extract_iocs, llm_extract_actors, validate_actors_against_text, should_use_llm_for_actors

LOG = logging.getLogger("enrichment.worker")

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")

def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v.strip())
    except ValueError:
        return default

async def _bulk_update(es: AsyncElasticsearch, ops: List[Dict[str, Any]]) -> None:
    if not ops:
        return
    try:
        await es.bulk(operations=ops, refresh=False)
    except Exception as e:
        LOG.warning("bulk update failed: %s", e)

async def _fetch_batch(es: AsyncElasticsearch, index_pattern: str, version: str, size: int, source_fields: List[str]) -> List[Dict[str, Any]]:
    body = {
        "query": {
            "bool": {
                "must_not": [
                    {"term": {"enrichment.version": version}}
                ]
            }
        },
        "size": size,
        "sort": ["_doc"],
        "_source": source_fields
    }
    r = await es.search(index=index_pattern, body=body)
    return ((r.get("hits") or {}).get("hits") or [])

async def main() -> None:
    logging.basicConfig(level=os.getenv("APP_LOG_LEVEL", "INFO").upper())
    settings = Settings()

    if not _env_bool("ENRICH_ENABLED", False):
        LOG.info("ENRICH_ENABLED=false, exiting.")
        return

    index_pattern = os.getenv("ENRICH_INDEX_PATTERN", "data_cached_*").strip()
    version = os.getenv("ENRICH_VERSION", "v1").strip()
    batch_size = _env_int("ENRICH_BATCH_SIZE", 200)
    interval = _env_int("ENRICH_INTERVAL_SECONDS", 30)
    auto_bootstrap = _env_bool("ENRICH_AUTO_BOOTSTRAP", True)

    text_fields = [x.strip() for x in (os.getenv("ENRICH_TEXT_FIELDS", "title,summary,description,content,message").split(",")) if x.strip()]
    dict_index = os.getenv("ENRICH_ACTOR_DICT_INDEX", "rbtn_actor_dictionary").strip()
    dict_refresh = _env_int("ENRICH_ACTOR_DICT_REFRESH_SECONDS", 300)
    use_llm = _env_bool("ENRICH_USE_LLM_FOR_ACTORS", True)

    # ES client (same basic auth + tls settings)
    es_kwargs = {
        "basic_auth": (settings.es_username, settings.es_password),
        "verify_certs": settings.es_verify_certs,
        "request_timeout": settings.es_request_timeout,
    }
    if settings.es_ca_certs:
        es_kwargs["ca_certs"] = settings.es_ca_certs
    if settings.es_ssl_assert_fingerprint:
        es_kwargs["ssl_assert_fingerprint"] = settings.es_ssl_assert_fingerprint

    es = AsyncElasticsearch(settings.es_url, **es_kwargs)

    llm = None
    if use_llm:
        llm = OpenAICompatibleClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=0.1,
            max_tokens=400,
        )

    try:
        if auto_bootstrap:
            LOG.info("Bootstrapping template/mappings/dictionary…")
            await bootstrap_all(es, index_pattern=index_pattern, dict_index=dict_index)

        actor_dict = ActorDictionary(es, dict_index=dict_index, refresh_seconds=dict_refresh)
        await actor_dict.load(force=True)

        # source fields: text + existing entity fields (optional) + enrichment
        source_fields = list(set(text_fields + [
            "threat", "entities", "enrichment",
        ]))

        LOG.info("Enricher started. pattern=%s batch=%d interval=%ds version=%s", index_pattern, batch_size, interval, version)

        while True:
            hits = await _fetch_batch(es, index_pattern, version, batch_size, source_fields)
            if not hits:
                await asyncio.sleep(interval)
                continue

            ops: List[Dict[str, Any]] = []
            processed = 0

            # refresh dictionary periodically
            await actor_dict.load(force=False)

            for h in hits:
                idx = h.get("_index")
                doc_id = h.get("_id")
                src = h.get("_source") or {}

                # collect text
                text, used_fields = collect_text(src, text_fields, max_chars=12000)
                if not text:
                    # still mark processed to avoid infinite loop
                    doc_patch = {
                        "enrichment": {
                            "version": version,
                            "processed_at": datetime.now(timezone.utc).isoformat(),
                            "source_fields": used_fields,
                        }
                    }
                    ops.append({"update": {"_index": idx, "_id": doc_id}})
                    ops.append({"doc": doc_patch})
                    processed += 1
                    continue

                iocs = extract_iocs(text)

                # actors: dictionary first
                actors = actor_dict.extract_from_text(text)

                # llm fallback only if cues present and dictionary found none
                if llm and not actors and should_use_llm_for_actors(text):
                    llm_actors = await llm_extract_actors(llm, text)
                    llm_actors = validate_actors_against_text(llm_actors, text)
                    actors = sorted(set(actors + llm_actors))

                # patch document
                now_iso = datetime.now(timezone.utc).isoformat()

                doc_patch: Dict[str, Any] = {
                    "entities": {
                        "threat_actors": actors,
                        "cves": iocs.get("cves", []),
                        "ips": iocs.get("ips", []),
                        "domains": iocs.get("domains", []),
                        "hashes": iocs.get("hashes", []),
                    },
                    # ECS-ish mirror
                    "threat": {
                        "group": {
                            "name": actors
                        }
                    },
                    "enrichment": {
                        "version": version,
                        "processed_at": now_iso,
                        "source_fields": used_fields,
                    }
                }

                ops.append({"update": {"_index": idx, "_id": doc_id}})
                ops.append({"doc": doc_patch})

                processed += 1

            await _bulk_update(es, ops)
            LOG.info("Enriched %d docs (version=%s)", processed, version)

            # short sleep to reduce pressure
            await asyncio.sleep(1)

    finally:
        try:
            await es.close()
        except Exception:
            pass
        if llm:
            try:
                await llm.close()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())

