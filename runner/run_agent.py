#!/usr/bin/env python3
"""
Claude Agent Runner

Reads AGENT.md from a mounted ConfigMap at /config/AGENT.md and runs
Claude in an agentic loop with MCP tool access. MCP servers are configured
via /config/mcp.json. The runner acts as an MCP client, calling each server
in-cluster over HTTP/SSE.

Environment variables:
  ANTHROPIC_API_KEY   Required. Anthropic API key.
  AGENT_MD_PATH       Path to AGENT.md. Default: /config/AGENT.md
  MCP_CONFIG_PATH     Path to mcp.json. Default: /config/mcp.json
  CLAUDE_MODEL        Claude model ID. Default: claude-opus-4-6
  MAX_TOKENS          Max tokens per response. Default: 8192
  MAX_TURNS           Max agentic loop turns before giving up. Default: 50
  BRIEFING_EMAIL      Passed as context to the agent prompt (optional).
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

AGENT_MD_PATH = os.environ.get("AGENT_MD_PATH", "/config/AGENT.md")
MCP_CONFIG_PATH = os.environ.get("MCP_CONFIG_PATH", "/config/mcp.json")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-6")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "8192"))
MAX_TURNS = int(os.environ.get("MAX_TURNS", "50"))
# Seconds to wait between turns — prevents hitting the RPM rate limit
TURN_DELAY = float(os.environ.get("TURN_DELAY", "15"))
# Truncate tool results to this many characters to keep input tokens in check
TOOL_RESULT_MAX_CHARS = int(os.environ.get("TOOL_RESULT_MAX_CHARS", "3000"))


def load_agent_prompt() -> str:
    path = Path(AGENT_MD_PATH)
    if not path.exists():
        raise FileNotFoundError(f"AGENT.md not found at {AGENT_MD_PATH}")
    content = path.read_text()

    # Substitute simple template variables
    from zoneinfo import ZoneInfo
    mountain = ZoneInfo("America/Denver")
    today = datetime.now(mountain)
    offset_secs = today.utcoffset().total_seconds()
    offset_hours = int(offset_secs // 3600)
    tz_offset = f"{offset_hours:+03d}:00"  # e.g. "-06:00" (MDT) or "-07:00" (MST)

    content = content.replace("{{ TODAY }}", today.strftime("%A, %B %-d, %Y"))
    content = content.replace("{{ DATE }}", today.strftime("%Y-%m-%d"))
    content = content.replace("{{ TIME }}", today.strftime("%-I:%M %p %Z"))
    content = content.replace("{{ TZ_OFFSET }}", tz_offset)

    # Inject optional env vars
    briefing_email = os.environ.get("BRIEFING_EMAIL", "")
    if briefing_email:
        content = content.replace("{{ BRIEFING_EMAIL }}", briefing_email)

    return content


def load_mcp_config() -> dict:
    path = Path(MCP_CONFIG_PATH)
    if not path.exists():
        log.warning("No mcp.json found at %s — running without MCP tools", MCP_CONFIG_PATH)
        return {"mcpServers": {}}

    raw = path.read_text()
    # Expand ${ENV_VAR} patterns in the config
    raw = re.sub(
        r"\$\{([^}]+)\}",
        lambda m: os.environ.get(m.group(1), m.group(0)),
        raw,
    )
    return json.loads(raw)


@asynccontextmanager
async def mcp_client(server_config: dict):
    """Open an MCP client session using the transport specified in server_config."""
    url = server_config["url"]
    headers = server_config.get("headers", {})
    transport = server_config.get("transport", "sse")
    if transport == "streamable_http":
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def mcp_tool_to_anthropic(tool: Tool) -> dict:
    """Convert MCP Tool definition to Anthropic tool input format."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


async def discover_mcp_tools(mcp_config: dict) -> tuple[list[dict], dict[str, dict]]:
    """Connect to each MCP server, collect tool definitions, and build routing table."""
    all_tools: list[dict] = []
    tool_server_map: dict[str, dict] = {}  # tool_name -> server_config

    for server_name, server_config in mcp_config.get("mcpServers", {}).items():
        log.info("Connecting to MCP server: %s at %s", server_name, server_config["url"])
        whitelist = set(server_config["tools"]) if "tools" in server_config else None
        try:
            async with mcp_client(server_config) as session:
                response = await session.list_tools()
                for tool in response.tools:
                    if whitelist and tool.name not in whitelist:
                        continue
                    anthropic_tool = mcp_tool_to_anthropic(tool)
                    all_tools.append(anthropic_tool)
                    tool_server_map[tool.name] = server_config
                    log.info("  Registered tool: %s", tool.name)
        except Exception as exc:
            log.error("Failed to connect to MCP server %s: %s", server_name, exc)

    log.info("Total tools available: %d", len(all_tools))
    return all_tools, tool_server_map


async def call_mcp_tool(tool_name: str, tool_input: dict, server_config: dict) -> str:
    """Invoke a tool on its MCP server and return the result as a string."""
    async with mcp_client(server_config) as session:
        result = await session.call_tool(tool_name, tool_input)

    if result.isError:
        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
        return f"Tool error: {' '.join(parts)}"

    parts = []
    for content in result.content:
        if hasattr(content, "text"):
            parts.append(content.text)
    return "\n".join(parts) if parts else "Tool executed successfully with no output"


def call_api_with_retry(client: anthropic.Anthropic, **kwargs) -> Any:
    """Call the Anthropic API, respecting Retry-After headers and backing off on rate limits."""
    max_retries = 6
    fallback_delay = 60  # used if no Retry-After header present

    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            if attempt == max_retries - 1:
                raise
            # Honour the Retry-After header when present
            retry_after = None
            if hasattr(exc, "response") and exc.response is not None:
                retry_after = exc.response.headers.get("retry-after")
            wait = int(retry_after) if retry_after else fallback_delay * (2 ** attempt)
            log.warning("Rate limited (attempt %d/%d) — waiting %ds", attempt + 1, max_retries, wait)
            time.sleep(wait)
        except anthropic.APIStatusError as exc:
            if exc.status_code == 529 and attempt < max_retries - 1:
                wait = fallback_delay * (2 ** attempt)
                log.warning("API overloaded (attempt %d/%d) — waiting %ds", attempt + 1, max_retries, wait)
                time.sleep(wait)
            else:
                raise


def truncate_tool_result(result: str) -> str:
    if len(result) <= TOOL_RESULT_MAX_CHARS:
        return result
    truncated = result[:TOOL_RESULT_MAX_CHARS]
    log.warning("Tool result truncated from %d to %d chars", len(result), TOOL_RESULT_MAX_CHARS)
    return truncated + f"\n... [truncated — {len(result) - TOOL_RESULT_MAX_CHARS} chars omitted]"


async def run_agent() -> None:
    prompt = load_agent_prompt()
    mcp_config = load_mcp_config()

    log.info("Starting agent run | model=%s max_turns=%d turn_delay=%ss", MODEL, MAX_TURNS, TURN_DELAY)
    log.info("Prompt length: %d characters", len(prompt))

    tools, tool_server_map = await discover_mcp_tools(mcp_config)

    client = anthropic.Anthropic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    for turn in range(1, MAX_TURNS + 1):
        log.info("--- Turn %d/%d ---", turn, MAX_TURNS)

        if turn > 1:
            log.info("Waiting %ss before next API call", TURN_DELAY)
            await asyncio.sleep(TURN_DELAY)

        kwargs: dict[str, Any] = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = call_api_with_retry(client, **kwargs)
        log.info("stop_reason=%s usage=%s", response.stop_reason, response.usage)

        # Add assistant response to conversation history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            log.info("Agent completed normally")
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    log.info("Final text output:\n%s", block.text[:500])
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                log.info(
                    "Calling tool: %s | input: %s",
                    block.name,
                    json.dumps(block.input)[:300],
                )

                server_config = tool_server_map.get(block.name)
                if server_config:
                    try:
                        result = await call_mcp_tool(block.name, block.input, server_config)
                    except Exception as exc:
                        result = f"Error calling tool {block.name}: {exc}"
                        log.error(result)
                else:
                    result = f"Error: Tool '{block.name}' not found in any connected MCP server"
                    log.warning(result)

                result = truncate_tool_result(result)
                log.info("Tool result (%d chars): %s...", len(result), result[:200])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "max_tokens":
            log.warning("Hit max_tokens limit on turn %d, continuing...", turn)
            # Continue — Claude may resume in the next turn

        else:
            log.warning("Unexpected stop_reason: %s — stopping", response.stop_reason)
            break

    else:
        log.warning("Reached max turns (%d) without end_turn", MAX_TURNS)

    log.info("Agent run complete")


if __name__ == "__main__":
    asyncio.run(run_agent())
