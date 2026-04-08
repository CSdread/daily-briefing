"""
MCP client utilities for the agent runner.

Handles connecting to MCP servers, discovering tools, and calling tools.
Supports both SSE and StreamableHTTP transports.
"""

import logging
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool

log = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_session(server_config: dict):
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
    """Convert an MCP Tool definition to the Anthropic tools input format."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema or {"type": "object", "properties": {}},
    }


async def discover_mcp_tools(mcp_config: dict) -> tuple[list[dict], dict[str, dict]]:
    """Connect to each MCP server, collect tool definitions, and build a routing table.

    Returns:
        tools: list of Anthropic-format tool dicts
        tool_server_map: {tool_name: server_config} for dispatch
    """
    all_tools: list[dict] = []
    tool_server_map: dict[str, dict] = {}

    for server_name, server_config in mcp_config.get("mcpServers", {}).items():
        log.info("Connecting to MCP server: %s at %s", server_name, server_config["url"])
        whitelist = set(server_config["tools"]) if "tools" in server_config else None

        try:
            async with mcp_session(server_config) as session:
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

    log.info("Total MCP tools available: %d", len(all_tools))
    return all_tools, tool_server_map


async def call_mcp_tool(tool_name: str, tool_input: dict, server_config: dict) -> str:
    """Invoke a tool on its MCP server and return the result as a string."""
    async with mcp_session(server_config) as session:
        result = await session.call_tool(tool_name, tool_input)

    if result.isError:
        parts = [c.text for c in result.content if hasattr(c, "text")]
        return f"Tool error: {' '.join(parts)}"

    parts = [c.text for c in result.content if hasattr(c, "text")]
    return "\n".join(parts) if parts else "Tool executed successfully with no output"
