import os
import re
import time
import json
from typing import Any, Dict, List, Tuple, Optional

try:
    from flashtext import KeywordProcessor  # type: ignore
except Exception:  # pragma: no cover
    KeywordProcessor = None

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")

ACTOR_CUE_RE = re.compile(r"\b(threat actor|adversary|intrusion set|apt\d+|ransomware|group)\b", re.IGNORECASE)

def get_nested(doc: Dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur

def collect_text(doc: Dict[str, Any], fields: List[str], max_chars: int = 12000) -> Tuple[str, List[str]]:
    parts = []
    used = []
    for f in fields:
        v = get_nested(doc, f) if "." in f else doc.get(f)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
            used.append(f)
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text, used

def extract_iocs(text: str) -> Dict[str, List[str]]:
    cves = sorted(set(m.group(0).upper() for m in CVE_RE.finditer(text)))
    ips = sorted(set(m.group(0) for m in IP_RE.finditer(text)))
    hashes = sorted(set(m.group(0).lower() for m in SHA256_RE.finditer(text)))
    hashes += sorted(set(m.group(0).lower() for m in SHA1_RE.finditer(text)))
    hashes += sorted(set(m.group(0).lower() for m in MD5_RE.finditer(text)))

    # domain regex can overmatch; keep basic filter (no IP-like, no trailing dot)
    domains = []
    for m in DOMAIN_RE.finditer(text):
        d = m.group(0).lower().strip(".")
        if d.count(".") >= 1 and not IP_RE.fullmatch(d):
            domains.append(d)
    domains = sorted(set(domains))

    return {"cves": cves, "ips": ips, "hashes": hashes, "domains": domains}

class ActorDictionary:
    def __init__(self, es, dict_index: str, refresh_seconds: int = 300):
        self.es = es
        self.dict_index = dict_index
        self.refresh_seconds = refresh_seconds
        self._last_load = 0.0
        self.alias_to_canonical: Dict[str, str] = {}
        self._kp = None

    async def load(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_load) < self.refresh_seconds and self.alias_to_canonical:
            return

        alias_map: Dict[str, str] = {}
        try:
            r = await self.es.search(index=self.dict_index, body={"query": {"match_all": {}}, "size": 10000, "_source": ["canonical", "aliases"]})
            hits = ((r.get("hits") or {}).get("hits") or [])
            for h in hits:
                src = h.get("_source") or {}
                canonical = (src.get("canonical") or "").strip()
                if not canonical:
                    continue
                aliases = src.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [aliases]
                # include canonical as alias too
                all_aliases = [canonical] + [a for a in aliases if isinstance(a, str)]
                for a in all_aliases:
                    aa = a.strip()
                    if aa:
                        alias_map[aa.lower()] = canonical
        except Exception:
            alias_map = {}

        self.alias_to_canonical = alias_map
        self._last_load = now

        if KeywordProcessor:
            kp = KeywordProcessor(case_sensitive=False)
            for alias in self.alias_to_canonical.keys():
                kp.add_keyword(alias)
            self._kp = kp
        else:
            self._kp = None

    def extract_from_text(self, text: str) -> List[str]:
        if not text:
            return []

        # Fast path: flashtext
        if self._kp:
            found = self._kp.extract_keywords(text)
            canon = {self.alias_to_canonical.get(f.lower(), f) for f in found}
            return sorted({c for c in canon if c})

        # Fallback: naive scan (slower)
        low = text.lower()
        out = set()
        for alias, canonical in self.alias_to_canonical.items():
            if alias and alias in low:
                out.add(canonical)
        return sorted(out)

async def llm_extract_actors(llm_client, text: str) -> List[str]:
    """
    Extract actors only if explicitly mentioned.
    """
    if not text:
        return []

    sys = "Extract threat actor / adversary group names ONLY if explicitly present in the text. Return JSON {\"actors\": [..]} with canonical names, no commentary."
    user = f"TEXT:\n{text}\n\nReturn JSON only."

    resp = await llm_client.chat(messages=[
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ])

    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    content = (msg.get("content") or "").strip()

    # Try parse JSON safely
    try:
        obj = json.loads(content)
        actors = obj.get("actors") or []
        if isinstance(actors, str):
            actors = [actors]
        actors = [a.strip() for a in actors if isinstance(a, str) and a.strip()]
        return sorted(set(actors))
    except Exception:
        return []

def validate_actors_against_text(actors: List[str], text: str) -> List[str]:
    low = text.lower()
    good = []
    for a in actors:
        if a and a.lower() in low:   # must literally appear (reduces hallucination)
            good.append(a)
    return sorted(set(good))

def should_use_llm_for_actors(text: str) -> bool:
    return bool(ACTOR_CUE_RE.search(text or ""))

