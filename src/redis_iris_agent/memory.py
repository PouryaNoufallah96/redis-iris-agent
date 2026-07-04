"""Redis Iris **Agent Memory** integration for the Pydantic AI agent.

Context Retriever gives the agent *business data* (live records). Agent Memory
gives it *conversation* memory:

* **Session (working / short-term) memory** — an ordered log of the current
  conversation's events, scoped by ``session_id``, with a TTL. The managed
  service automatically summarizes/trims it and, in the background,
  **promotes** high-signal facts from it into long-term memory. That background
  promotion is the whole point: durable memory with no promotion/pruning code.
* **Long-term memory** — cross-session facts (preferences, past decisions)
  persisted per user and retrieved by **semantic (vector) search + metadata
  filters**, regardless of which session created them.

We expose long-term memory to the model as two ordinary tools
(``search_memory`` / ``store_memory``) so you *see* the recall/write happen as
tool calls right next to the Context Retriever tools — one context layer, all
tools. We also log each turn as a session event so the service's background
extraction can promote durable facts on its own.

Managed SDK (``redis-agent-memory``) surface, per Redis's official
``agent_memory.ipynb`` notebook:
    AgentMemory(endpoint, store_id=..., api_key=...)
    .add_session_event_async(session_id, actor_id, role, content, created_at)
    .search_long_term_memory_async(request={"text": ..., "filter": {...}})
    .bulk_create_long_term_memories_async(memories=[{id, owner_id, text}])
"""

from __future__ import annotations

import time
from dataclasses import dataclass

try:  # optional dependency — Context Retriever works without it
    from redis_agent_memory import AgentMemory, models

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001 - surface a friendly message later
    AgentMemory = None  # type: ignore[assignment]
    models = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc

from .config import Settings


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Identity:
    """Whose memory we read/write.

    Mutable on purpose: ``/newsession`` rotates ``session_id`` while keeping the
    same ``owner_id``, which is exactly how we demo cross-session recall — a fact
    stored (or auto-promoted) under one session is recalled in the next, because
    long-term memory is keyed on the *user*, not the session.
    """

    owner_id: str
    session_id: str


class MemoryService:
    """Thin wrapper over the managed Agent Memory client, bound to an identity."""

    def __init__(self, client: "AgentMemory", identity: Identity) -> None:
        self._client = client
        self.identity = identity

    @classmethod
    def from_settings(cls, settings: Settings, identity: Identity) -> "MemoryService":
        if AgentMemory is None:  # pragma: no cover - import guard
            raise RuntimeError(
                "Agent Memory is configured but the 'redis-agent-memory' package "
                f"is not importable: {_IMPORT_ERROR!r}. Run `uv sync`."
            )
        client = AgentMemory(
            settings.memory_endpoint,
            store_id=settings.memory_store_id,
            api_key=settings.memory_key,
        )
        return cls(client, identity)

    async def health(self) -> bool:
        """Best-effort reachability check; never raises."""
        try:
            await self._client.health_async()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def log_turn(self, role: str, text: str) -> None:
        """Append one conversation event to session (working) memory.

        ``role`` is "user" or "assistant". Failures are swallowed — memory
        logging must never break the chat loop.
        """
        if not text:
            return
        try:
            msg_role = (
                models.MessageRole.USER
                if role == "user"
                else models.MessageRole.ASSISTANT
            )
            await self._client.add_session_event_async(
                session_id=self.identity.session_id,
                actor_id=self.identity.owner_id,
                role=msg_role,
                content=[{"text": text}],
                created_at=_now_ms(),
            )
        except Exception:  # noqa: BLE001
            pass

    async def search(self, query: str) -> str:
        """Semantic search over THIS user's long-term memory. Returns JSON text."""
        res = await self._client.search_long_term_memory_async(
            request={
                "text": query,
                "filter": {"owner_id": {"eq": self.identity.owner_id}},
            }
        )
        return _to_text(res)

    async def store(self, fact: str) -> str:
        """Persist a durable fact to THIS user's long-term memory (immediate)."""
        memory_id = f"{self.identity.owner_id}-{_now_ms()}"
        res = await self._client.bulk_create_long_term_memories_async(
            memories=[
                {
                    "id": memory_id,
                    "owner_id": self.identity.owner_id,
                    "text": fact,
                }
            ]
        )
        return _to_text(res)


def _to_text(obj: object) -> str:
    """Coerce an SDK response (pydantic model or plain) into a string for the LLM."""
    for attr in ("model_dump_json", "json"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                break
    return str(obj)
