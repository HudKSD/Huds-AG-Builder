import time
from typing import Any, Dict, List, Tuple
from .config import Settings

def _flatten_properties(props: Dict[str, Any], prefix: str, out: Dict[str, str]) -> None:
    for name, spec in props.items():
        path = f"{prefix}{name}" if prefix else name
        ftype = spec.get("type", "object")
        out[path] = ftype

        # Multi-fields
        if isinstance(spec.get("fields"), dict):
            for subname, subspec in spec["fields"].items():
                out[f"{path}.{subname}"] = subspec.get("type", "keyword")

        # Nested properties
        if isinstance(spec.get("properties"), dict):
            _flatten_properties(spec["properties"], f"{path}.", out)

class SchemaCache:
    """
    Caches flattened mappings per allowed pattern (TTL).
    """
    def __init__(self, es_client, settings: Settings, ttl_seconds: int = 300):
        self.es = es_client
        self.settings = settings
        self.ttl = ttl_seconds
        self._cache: Dict[str, Tuple[float, Dict[str, str], List[str]]] = {}

    async def get_flat_fields(self, pattern: str) -> Tuple[Dict[str, str], List[str]]:
        now = time.time()
        cached = self._cache.get(pattern)
        if cached and (now - cached[0] < self.ttl):
            return cached[1], cached[2]

        resp = await self.es.indices.get_mapping(index=pattern, allow_no_indices=True)
        fields: Dict[str, str] = {}
        date_fields: List[str] = []

        for _, idx_obj in (resp or {}).items():
            mappings = (idx_obj or {}).get("mappings", {})
            props = mappings.get("properties", {})
            if isinstance(props, dict):
                _flatten_properties(props, "", fields)

        for f, t in fields.items():
            if t in ("date", "date_nanos"):
                date_fields.append(f)

        # prefer @timestamp first if present
        date_fields = sorted(date_fields, key=lambda x: (0 if x == "@timestamp" else 1, x))

        self._cache[pattern] = (now, fields, date_fields)
        return fields, date_fields

    async def get_index_cards(self) -> List[Dict[str, Any]]:
        cards: List[Dict[str, Any]] = []
        for pat in self.settings.es_allowed_patterns:
            fields, date_fields = await self.get_flat_fields(pat)
            sample_fields = list(fields.items())[:120]
            cards.append({
                "pattern": pat,
                "field_count": len(fields),
                "sample_fields": [{"field": k, "type": v} for k, v in sample_fields],
                "date_fields": date_fields[:10],
            })
        return cards

