"""Builds the Pydantic AI agent wired to the Redis Iris Context Retriever.

The Context Retriever exposes a standard streamable-HTTP MCP server at
``/mcp``, authenticated with an ``X-API-Key`` header. Pydantic AI has a native
MCP client, so we point an ``MCPToolset`` straight at that endpoint and hand it
to the ``Agent`` as a toolset. The model then discovers and calls the
auto-generated tools (``get_*_by_id``, ``filter_*_by_*``, ``search_*_by_text``,
``find_*_by_*_range``) with no per-query code on our side.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from .config import Settings

if TYPE_CHECKING:
    from .memory import MemoryService

# LLM providers constrain tool names. Anthropic requires ^[a-zA-Z0-9_-]{1,128}$;
# OpenAI is similar. Context Retriever derives tool names from your entity names,
# so an entity like "job queue" yields "get_job queue_by_id" (a space) which the
# provider rejects. We rename such tools to a safe form and map back on call.
_SAFE_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

SYSTEM_PROMPT = """\
You are a helpful data assistant connected to a Redis Iris Context Retriever \
over MCP. Your tools are auto-generated from the user's own data model, so the \
available entities and fields come entirely from those tools.

Guidelines:
- Discover what you can answer by looking at the tools available to you. Tool \
names encode the query pattern: `get_<entity>_by_id` (fetch one record), \
`filter_<entity>_by_<field>` (exact match), `search_<entity>_by_text` \
(full-text keyword search), and `find_<entity>_by_<field>_range` \
(numeric ranges).
- Call the tools to ground every factual answer in the live data. Never invent \
records, ids, or field values.
- If a question can't be answered with the available tools, say so plainly and \
suggest what data/tool would be needed.
- Prefer concise, well-structured answers. When you list records, summarize the \
fields that matter to the question rather than dumping raw JSON.
"""

MEMORY_PROMPT = """\

You also have long-term memory for THIS user, exposed as two tools:
- `search_memory`: recall durable facts about the user (their stated \
preferences, past decisions, prior context). Call it BEFORE answering anything \
that depends on who the user is or what they told you earlier — even in a brand \
new conversation, because memory persists across sessions.
- `store_memory`: persist a durable fact about the user. Call it whenever they \
share a preference, decision, or detail worth remembering next time (e.g. "I \
prefer road bikes", "my budget is $500"). Store the fact plainly, in one line.

Memory is about the *user*; the Context Retriever tools are about the *data*. A \
good answer often uses both: recall what the user wants, then query live data to \
satisfy it.
"""


def build_toolset(settings: Settings) -> MCPToolset:
    """Create the Context Retriever MCP toolset."""
    return MCPToolset(
        settings.mcp_url,
        headers={"X-API-Key": settings.agent_key},
    )


def safe_name_map(tool_names: list[str]) -> dict[str, str]:
    """Build a ``{safe_name: original_name}`` map for tool names an LLM provider
    would reject. Returns only the entries that actually need renaming; pass the
    result to ``toolset.renamed(...)`` (unmapped tools pass through unchanged).
    """
    used = set(tool_names)
    name_map: dict[str, str] = {}
    for original in tool_names:
        if _SAFE_TOOL_NAME.match(original):
            continue
        base = (re.sub(r"[^a-zA-Z0-9_-]", "_", original)[:128]) or "tool"
        candidate, i = base, 2
        while candidate in used or candidate in name_map:
            suffix = f"_{i}"
            candidate = base[: 128 - len(suffix)] + suffix
            i += 1
        name_map[candidate] = original
        used.add(candidate)
    return name_map


def build_agent(
    settings: Settings,
    toolset: MCPToolset,
    memory: "MemoryService | None" = None,
) -> Agent:
    """Create the Pydantic AI agent backed by the Context Retriever tools.

    When ``memory`` is provided, the agent also gets ``search_memory`` /
    ``store_memory`` tools over the user's Agent Memory, plus guidance on when to
    use them. Context Retriever = live data; Agent Memory = who the user is.
    """
    instructions = SYSTEM_PROMPT + (MEMORY_PROMPT if memory else "")
    agent = Agent(
        settings.model,
        toolsets=[toolset],
        instructions=instructions,
    )
    if memory is not None:
        _attach_memory_tools(agent, memory)
    return agent


def _attach_memory_tools(agent: Agent, memory: "MemoryService") -> None:
    """Register long-term memory as two plain tools bound to the current user.

    Identity (owner/session) lives on ``memory.identity`` — the model never
    passes it, so it can't read or write another user's memory. That server-side
    scoping is the multi-user point a folder of markdown can't give you.
    """

    @agent.tool_plain
    async def search_memory(query: str) -> str:
        """Recall durable facts about the current user from long-term memory
        (their preferences, past decisions, prior context). Use before answering
        anything that depends on who the user is or what they told you before."""
        return await memory.search(query)

    @agent.tool_plain
    async def store_memory(fact: str) -> str:
        """Persist a durable fact about the current user (a stated preference,
        decision, or detail worth remembering next time). One concise line."""
        return await memory.store(fact)
