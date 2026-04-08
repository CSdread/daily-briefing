#!/usr/bin/env python3
"""
Claude Agent Runner

Reads AGENT.md from a mounted ConfigMap at /config/AGENT.md and runs
Claude in an agentic loop with MCP tool access. MCP servers are configured
via /config/mcp.json. The runner acts as an MCP client, calling each server
in-cluster over HTTP/SSE.

Built-in memory tools (memory_read, memory_write, memory_list, memory_delete)
are also registered and handled in-process for reading/writing /memory.

Environment variables:
  ANTHROPIC_API_KEY   Required. Anthropic API key.
  AGENT_MD_PATH       Path to AGENT.md. Default: /config/AGENT.md
  MCP_CONFIG_PATH     Path to mcp.json. Default: /config/mcp.json
  MEMORY_PATH         Path to memory volume. Default: /memory
  CLAUDE_MODEL        Claude model ID. Default: claude-opus-4-6
  MAX_TOKENS          Max tokens per response. Default: 8192
  MAX_TURNS           Max agentic loop turns before giving up. Default: 50
  TURN_DELAY          Seconds between turns (rate limit buffer). Default: 15
  TOOL_RESULT_MAX_CHARS  Truncate tool results to this length. Default: 3000
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

from mcp_client import discover_mcp_tools, call_mcp_tool
from memory import BUILTIN_TOOLS, BUILTIN_TOOL_NAMES, call_builtin_tool

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


def call_api_with_retry(client: anthropic.Anthropic, **kwargs) -> Any:
    """Call the Anthropic API, respecting Retry-After headers and backing off on rate limits."""
    max_retries = 6
    fallback_delay = 60

    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            if attempt == max_retries - 1:
                raise
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


def compact_messages(messages: list, keep_recent: int = 2) -> list:
    """Replace tool result content in older turns with a short placeholder.

    Keeps the last `keep_recent` tool-result exchanges at full fidelity so the
    model retains recent context. Older exchanges are collapsed to a one-liner
    so their tool_use_id references remain valid without burning tokens.
    """
    tr_indices = [
        i for i, m in enumerate(messages)
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in m["content"])
    ]
    to_compact = tr_indices[:-keep_recent] if len(tr_indices) > keep_recent else []
    for idx in to_compact:
        messages[idx] = {
            **messages[idx],
            "content": [
                (
                    {"type": "tool_result", "tool_use_id": c["tool_use_id"], "content": "[compacted]"}
                    if isinstance(c, dict) and c.get("type") == "tool_result"
                    else c
                )
                for c in messages[idx]["content"]
            ],
        }
    if to_compact:
        log.debug("Compacted %d old tool-result exchange(s)", len(to_compact))
    return messages


async def run_agent() -> None:
    prompt = load_agent_prompt()
    mcp_config = load_mcp_config()

    log.info("Starting agent run | model=%s max_turns=%d turn_delay=%ss", MODEL, MAX_TURNS, TURN_DELAY)
    log.info("Prompt length: %d characters", len(prompt))

    mcp_tools, tool_server_map = await discover_mcp_tools(mcp_config)

    # Combine MCP tools with built-in memory tools.
    # Memory tools go last so cache_control lands on a stable entry.
    tools = mcp_tools + BUILTIN_TOOLS
    log.info("Built-in tools registered: %s", sorted(BUILTIN_TOOL_NAMES))

    # Mark the last tool for prompt caching — Anthropic caches everything up to
    # this point across turns, so tool definitions don't burn TPM on every call.
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

    client = anthropic.Anthropic()
    # System prompt is static across all turns — ideal for caching.
    system = [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [{"role": "user", "content": "Begin."}]

    for turn in range(1, MAX_TURNS + 1):
        log.info("--- Turn %d/%d ---", turn, MAX_TURNS)

        if turn > 1:
            log.info("Waiting %ss before next API call", TURN_DELAY)
            await asyncio.sleep(TURN_DELAY)

        messages = compact_messages(messages)
        kwargs: dict[str, Any] = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = call_api_with_retry(client, **kwargs)
        log.info("stop_reason=%s usage=%s", response.stop_reason, response.usage)

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

                if block.name in BUILTIN_TOOL_NAMES:
                    result = call_builtin_tool(block.name, block.input)
                else:
                    server_config = tool_server_map.get(block.name)
                    if server_config:
                        try:
                            result = await call_mcp_tool(block.name, block.input, server_config)
                        except Exception as exc:
                            result = f"Error calling tool {block.name}: {exc}"
                            log.error(result)
                    else:
                        result = f"Error: tool '{block.name}' not found in any connected MCP server"
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

        else:
            log.warning("Unexpected stop_reason: %s — stopping", response.stop_reason)
            break

    else:
        log.warning("Reached max turns (%d) without end_turn", MAX_TURNS)

    log.info("Agent run complete")


if __name__ == "__main__":
    asyncio.run(run_agent())
