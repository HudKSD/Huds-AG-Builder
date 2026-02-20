import uuid
from typing import Any, Dict, List, Optional

class ConversationStore:
    """
    In-memory conversation store.
    Easy to swap later for Redis/Postgres/Elasticsearch index.
    """
    def __init__(self, max_turns: int = 30):
        self.max_turns = max_turns
        self._store: Dict[str, List[Dict[str, Any]]] = {}
        self._meta: Dict[str, Dict[str, Any]] = {}

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def get(self, conversation_id: str) -> List[Dict[str, Any]]:
        return self._store.get(conversation_id, [])

    def set(self, conversation_id: str, messages: List[Dict[str, Any]]) -> None:
        # Keep last N turns (roughly)
        if len(messages) > self.max_turns * 2:
            messages = messages[-self.max_turns * 2 :]
        self._store[conversation_id] = messages

    def append(self, conversation_id: str, msg: Dict[str, Any]) -> None:
        msgs = self._store.get(conversation_id, [])
        msgs.append(msg)
        self.set(conversation_id, msgs)

    def get_meta(self, conversation_id: str) -> Dict[str, Any]:
        return dict(self._meta.get(conversation_id, {}))

    def set_meta(self, conversation_id: str, meta: Dict[str, Any]) -> None:
        if not isinstance(meta, dict):
            return
        self._meta[conversation_id] = dict(meta)

    def update_meta(self, conversation_id: str, **kwargs: Any) -> None:
        meta = self._meta.get(conversation_id, {})
        meta.update(kwargs)
        self._meta[conversation_id] = meta
