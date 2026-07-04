"""Configuration loading for the Redis Iris Context Retriever agent.

All settings come from environment variables (loaded from a local ``.env`` via
python-dotenv). The only *required* value is the Context Retriever agent key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Default MCP endpoint for the Context Retriever. This is the region-pinned host
# Redis documents in the official build tutorial's `.env`
# (CTX_MCP_URL=https://gcp-us-east4.context-surfaces.redis.io/mcp). Override with
# CTX_MCP_URL if your service lives in a different region.
DEFAULT_MCP_URL = "https://gcp-us-east4.context-surfaces.redis.io/mcp"

# Sensible default model. Any Pydantic AI model string works (e.g.
# "openai:gpt-4o", "google-gla:gemini-2.5-pro"); just set the matching API key.
DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(slots=True)
class Settings:
    """Resolved runtime settings."""

    agent_key: str
    mcp_url: str
    model: str
    # Agent Memory (managed Redis Iris service) — all three required to enable
    # memory. When any is missing, the agent runs Context-Retriever-only and the
    # memory tools are simply not registered.
    memory_endpoint: str | None = None
    memory_store_id: str | None = None
    memory_key: str | None = None

    @property
    def memory_enabled(self) -> bool:
        """True only when all three Agent Memory values are present."""
        return bool(self.memory_endpoint and self.memory_store_id and self.memory_key)


def load_settings() -> Settings:
    """Load and validate settings from the environment / .env file."""
    load_dotenv()  # no-op if there is no .env; env vars still win

    agent_key = os.getenv("CONTEXT_RETRIEVER_AGENT_KEY", "").strip()
    if not agent_key:
        raise ConfigError(
            "CONTEXT_RETRIEVER_AGENT_KEY is not set.\n"
            "Create a Context Retriever agent key in the Redis Cloud console "
            "(your service page -> 'Agent key' tab -> 'New Agent Key'), then add "
            "it to a .env file:\n\n"
            "    CONTEXT_RETRIEVER_AGENT_KEY=your-agent-key-here\n\n"
            "See .env.example for the full template."
        )

    return Settings(
        agent_key=agent_key,
        mcp_url=os.getenv("CTX_MCP_URL", "").strip() or DEFAULT_MCP_URL,
        model=os.getenv("MODEL", "").strip() or DEFAULT_MODEL,
        memory_endpoint=os.getenv("AGENT_MEMORY_ENDPOINT", "").strip() or None,
        memory_store_id=os.getenv("AGENT_MEMORY_STORE_ID", "").strip() or None,
        memory_key=os.getenv("AGENT_MEMORY_KEY", "").strip() or None,
    )
