"""The hero demo: Context Retriever + Agent Memory together, on the support data.

Prereqs (all in .env): CONTEXT_RETRIEVER_AGENT_KEY (Northpeak surface), the three
AGENT_MEMORY_* values, an LLM provider key, and the Northpeak data loaded
(seed_northpeak.py) with the surface provisioned (configure_surface.py).

Flow:
  support-1 (Jordan / customer C1004): states a durable preference -> store_memory.
  new session -> support-2 (fresh working memory, same customer).
  support-2: "why is my order late, and handle it like last time?" -> the agent
    RECALLS the preference (Agent Memory) AND looks up the delayed order + shipment
    (Context Retriever) -> one grounded answer. Both halves together, across sessions.

    uv run python demo_hero.py
"""
from __future__ import annotations

import asyncio
import logging

logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)

from pydantic_ai.messages import ToolCallPart

from redis_iris_agent.agent import build_agent, build_toolset, safe_name_map
from redis_iris_agent.config import load_settings
from redis_iris_agent.memory import Identity, MemoryService


def show_tool_calls(new_messages) -> None:
    for message in new_messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolCallPart):
                args = part.args_as_dict() if hasattr(part, "args_as_dict") else part.args
                print(f"   [tool] {part.tool_name}  {args}")


async def main() -> int:
    settings = load_settings()
    if not settings.memory_enabled:
        print("Agent Memory not configured (set the AGENT_MEMORY_* values).")
        return 2

    # Identity == the customer id, so memory and the support data line up.
    identity = Identity(owner_id="C1004", session_id="support-1")
    memory = MemoryService.from_settings(settings, identity)
    print("memory health:", await memory.health())

    toolset = build_toolset(settings)
    names = [t.name for t in await toolset.list_tools()]
    print(f"{len(names)} Context Retriever tools live")
    agent_toolset = toolset.renamed(safe_name_map(names)) if safe_name_map(names) else toolset
    agent = build_agent(settings, agent_toolset, memory=memory)

    async with agent:
        q1 = ("Hi, this is Jordan Rivera, customer C1004. For future reference: "
              "whenever an order of mine is delayed, I always want it reshipped "
              "expedited, never refunded. Please remember that.")
        print(f"\n=== support-1 > {q1}")
        await memory.log_turn("user", q1)
        r1 = await agent.run(q1)
        show_tool_calls(r1.new_messages())
        print("--- answer ---\n", r1.output)
        await memory.log_turn("assistant", r1.output)

        identity.session_id = "support-2"
        print("\n--- new session -> support-2 (working memory cleared) ---")

        q2 = ("It's Jordan Rivera again, customer C1004. Why is my order late, "
              "and can you handle it the way I asked last time?")
        print(f"\n=== support-2 > {q2}")
        await memory.log_turn("user", q2)
        r2 = await agent.run(q2)  # no message_history -> proves cross-session recall
        show_tool_calls(r2.new_messages())
        print("--- answer ---\n", r2.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
