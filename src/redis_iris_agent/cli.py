"""Colorful conversational CLI for the Redis Iris Context Retriever agent.

Keeps full conversation history in memory (Pydantic AI ``message_history``),
shows which Context Retriever MCP tools the agent called for each answer, and
supports a few slash commands.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ToolCallPart
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .agent import build_agent, build_toolset, safe_name_map
from .config import ConfigError, Settings, load_settings
from .memory import Identity, MemoryService

console = Console()

# Demo identity. In a real multi-user app these come from your auth layer; here
# we hardcode one user and rotate the session id with /newsession.
DEFAULT_OWNER_ID = "demo-user"
DEFAULT_SESSION_ID = "session-1"

HELP = """\
[bold]Commands[/bold]
  [cyan]/help[/cyan]        Show this help
  [cyan]/tools[/cyan]       List the Context Retriever tools the agent can call
  [cyan]/clear[/cyan]       Clear the conversation history (fresh context)
  [cyan]/newsession[/cyan]  Start a NEW session for the same user (clears working
              memory but long-term memory persists — the recall demo)
  [cyan]/whoami[/cyan]      Show the current user id / session id
  [cyan]/exit[/cyan]        Quit (also: /quit, Ctrl-D)
"""


def _banner(settings: Settings, identity: Identity, memory_on: bool) -> Panel:
    body = Text()
    body.append("Redis Iris ", style="bold red")
    body.append("agent\n", style="bold white")
    body.append("Ask about your data in plain English. Live data comes through "
                "the Context Retriever MCP tools; ", style="white")
    if memory_on:
        body.append("what you tell it is remembered across sessions via Agent "
                    "Memory.\n\n", style="white")
    else:
        body.append("Agent Memory is off (data only).\n\n", style="white")
    body.append("model    ", style="dim")
    body.append(f"{settings.model}\n", style="cyan")
    body.append("data     ", style="dim")
    body.append(f"Context Retriever  {settings.mcp_url}\n", style="cyan")
    body.append("memory   ", style="dim")
    if memory_on:
        body.append(f"Agent Memory  (user={identity.owner_id})\n", style="cyan")
    else:
        body.append("off\n", style="yellow")
    body.append("\nType ", style="dim")
    body.append("/help", style="cyan")
    body.append(" for commands.", style="dim")
    return Panel(body, border_style="red", title="[bold]iris[/bold]", expand=False)


def _render_tool_calls(new_messages: list[ModelMessage]) -> None:
    """Print a dim line for each Context Retriever tool the agent invoked."""
    calls: list[ToolCallPart] = []
    for message in new_messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolCallPart):
                calls.append(part)
    for call in calls:
        args = call.args_as_dict() if hasattr(call, "args_as_dict") else call.args
        console.print(
            Text.assemble(
                ("  ↳ ", "dim"),
                (call.tool_name, "green"),
                (f"  {args}", "dim"),
            )
        )


async def _list_tools(toolset) -> None:
    try:
        tools = await toolset.list_tools()
    except Exception as exc:  # noqa: BLE001 - surface any transport error nicely
        console.print(f"[red]Could not list tools:[/red] {exc}")
        return

    if not tools:
        console.print("[yellow]No tools available.[/yellow] "
                      "Is the Context Retriever service configured with entities?")
        return

    table = Table(title=f"Context Retriever tools ({len(tools)})",
                  title_style="bold red", border_style="dim", show_lines=False)
    table.add_column("Tool", style="green", no_wrap=True)
    table.add_column("Description", style="white")
    for tool in tools:
        desc = (getattr(tool, "description", "") or "").strip().split("\n")[0]
        table.add_row(getattr(tool, "name", "?"), desc)
    console.print(table)


async def _run() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        console.print(Panel(str(exc), title="[red]Configuration needed[/red]",
                            border_style="red"))
        return 1

    toolset = build_toolset(settings)

    # Fetch the tool names up front so we can (a) greet with the live count and
    # (b) rename any tools whose auto-generated names an LLM provider rejects
    # (e.g. an entity named "job queue" -> "get_job queue_by_id" with a space).
    tool_names: list[str] | None = None
    connect_error: str | None = None
    try:
        tool_names = [t.name for t in await toolset.list_tools()]
    except Exception as exc:  # noqa: BLE001
        connect_error = str(exc)

    name_map = safe_name_map(tool_names) if tool_names else {}
    agent_toolset = toolset.renamed(name_map) if name_map else toolset

    # Agent Memory (optional). Only wired in when all three memory values are set;
    # otherwise the agent is Context-Retriever-only and nothing memory-related runs.
    identity = Identity(owner_id=DEFAULT_OWNER_ID, session_id=DEFAULT_SESSION_ID)
    memory: MemoryService | None = None
    memory_note = ""
    if settings.memory_enabled:
        try:
            memory = MemoryService.from_settings(settings, identity)
            reachable = await memory.health()
            memory_note = ("" if reachable else
                           "  [yellow](memory endpoint not reachable — check "
                           "AGENT_MEMORY_* values)[/yellow]")
        except Exception as exc:  # noqa: BLE001
            memory = None
            memory_note = f"  [yellow](memory disabled: {exc})[/yellow]"

    try:
        agent = build_agent(settings, agent_toolset, memory=memory)
    except UserError as exc:
        console.print(Panel(
            f"{exc}\n\n"
            f"Your Context Retriever key is set, but the LLM provider for "
            f"model [cyan]{settings.model}[/cyan] needs its API key.\n"
            f"Add the matching key (e.g. ANTHROPIC_API_KEY / OPENAI_API_KEY) to "
            f"your .env, or change MODEL to a provider you have a key for.",
            title="[red]LLM provider key needed[/red]", border_style="red"))
        return 1

    console.print(_banner(settings, identity, memory_on=memory is not None))
    if memory_note:
        console.print(memory_note)

    if tool_names is not None:
        note = (f"  [dim]({len(name_map)} name(s) sanitized for the model)[/dim]"
                if name_map else "")
        console.print(f"[dim]Connected. [green]{len(tool_names)}[/green] "
                      f"Context Retriever tools available.[/dim]{note}\n")
    else:
        hint = ""
        if connect_error and ("401" in connect_error or "403" in connect_error):
            hint = ("  [dim]Looks like an auth failure — check "
                    "CONTEXT_RETRIEVER_AGENT_KEY (and CTX_MCP_URL region).[/dim]")
        console.print(f"[yellow]Could not reach the Context Retriever:[/yellow] "
                      f"{connect_error}{hint}\n")

    session: PromptSession[str] = PromptSession(history=InMemoryHistory())
    history: list[ModelMessage] = []

    # `async with agent` opens the MCP connection once and keeps it open for the
    # whole chat session, instead of reconnecting on every message.
    async with agent:

        while True:
            try:
                user_input = (await session.prompt_async("you › ")).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return 0

            if not user_input:
                continue

            lowered = user_input.lower()
            if lowered in ("/exit", "/quit"):
                console.print("[dim]bye[/dim]")
                return 0
            if lowered == "/help":
                console.print(HELP)
                continue
            if lowered == "/clear":
                history = []
                console.print("[dim]conversation history cleared[/dim]\n")
                continue
            if lowered == "/whoami":
                console.print(f"[dim]user[/dim] [cyan]{identity.owner_id}[/cyan]  "
                              f"[dim]session[/dim] [cyan]{identity.session_id}[/cyan]"
                              f"  [dim]memory[/dim] "
                              f"{'[green]on[/green]' if memory else '[yellow]off[/yellow]'}\n")
                continue
            if lowered == "/newsession":
                # Same user, brand-new session: working-memory context resets, but
                # long-term memory persists. This is the cross-session recall demo.
                history = []
                n = identity.session_id.rsplit("-", 1)
                nxt = f"{n[0]}-{int(n[1]) + 1}" if len(n) == 2 and n[1].isdigit() \
                    else f"{identity.session_id}-2"
                identity.session_id = nxt
                if memory:
                    console.print(f"[dim]new session[/dim] [cyan]{nxt}[/cyan] "
                                  f"[dim]for[/dim] [cyan]{identity.owner_id}[/cyan]"
                                  f"[dim] — working memory cleared, long-term memory "
                                  f"persists. Ask it what it remembers.[/dim]\n")
                else:
                    console.print(f"[dim]new session[/dim] [cyan]{nxt}[/cyan] "
                                  f"[yellow](memory off — nothing persists)[/yellow]\n")
                continue
            if lowered == "/tools":
                await _list_tools(toolset)
                continue

            # Log the user's turn into session (working) memory. The service
            # summarizes/trims it and promotes durable facts to long-term in the
            # background — no promotion code on our side.
            if memory:
                await memory.log_turn("user", user_input)

            try:
                with console.status("[red]thinking…[/red]", spinner="dots"):
                    result = await agent.run(user_input, message_history=history)
            except Exception as exc:  # noqa: BLE001 - keep the chat loop alive
                console.print(f"[red]Error:[/red] {exc}\n")
                continue

            _render_tool_calls(result.new_messages())
            console.print()
            console.print(Text("iris ›", style="bold red"))
            console.print(Markdown(result.output))
            console.print()

            if memory:
                await memory.log_turn("assistant", result.output)

            # Carry the full running transcript so the next turn has context.
            history = result.all_messages()


def main() -> None:
    """Console-script entry point."""
    # The Context Retriever server doesn't implement the optional MCP
    # session-DELETE endpoint, so the client logs a harmless "Session
    # termination failed: 404" warning on close. Hush it.
    logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
