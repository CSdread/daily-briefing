"""
Built-in memory tools for reading/writing agent state at /memory.

These are registered as native tools alongside MCP tools and handled
in-process by the runner — no MCP server required.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

MEMORY_PATH = Path(os.environ.get("MEMORY_PATH", "/memory"))

BUILTIN_TOOLS = [
    {
        "name": "memory_read",
        "description": (
            "Read a file from agent memory. Path is relative to /memory "
            "(e.g. 'index.md', 'people/jenn.json'). "
            "Returns file contents or an error if not found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to /memory"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "memory_write",
        "description": (
            "Write or overwrite a file in agent memory. Path is relative to /memory. "
            "Creates parent directories as needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to /memory"},
                "content": {"type": "string", "description": "File contents to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "memory_list",
        "description": (
            "List files and directories inside a /memory subdirectory. "
            "Use path '' for the memory root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to /memory (use '' for root)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "memory_delete",
        "description": "Delete a file from agent memory. Path is relative to /memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to /memory"},
            },
            "required": ["path"],
        },
    },
]

BUILTIN_TOOL_NAMES = {t["name"] for t in BUILTIN_TOOLS}


def call_builtin_tool(name: str, tool_input: dict) -> str:
    """Dispatch a memory_* tool call, handling it entirely in-process."""
    if not MEMORY_PATH.exists():
        return "Error: memory volume not mounted — /memory does not exist"

    try:
        raw = tool_input.get("path", "")
        target = (MEMORY_PATH / raw).resolve()

        # Guard against path traversal
        if not str(target).startswith(str(MEMORY_PATH.resolve())):
            return "Error: path traversal not allowed"

        if name == "memory_read":
            if not target.exists():
                return f"Error: not found: {raw}"
            return target.read_text(encoding="utf-8")

        elif name == "memory_write":
            content = tool_input.get("content", "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            log.info("memory_write: wrote %d bytes to %s", len(content), raw)
            return f"OK: wrote {len(content)} bytes to {raw}"

        elif name == "memory_list":
            if not target.exists():
                return f"Error: directory not found: {raw}"
            entries = sorted(e.relative_to(MEMORY_PATH) for e in target.iterdir())
            return "\n".join(str(e) for e in entries) if entries else "(empty)"

        elif name == "memory_delete":
            if not target.exists():
                return f"Not found (already deleted?): {raw}"
            target.unlink()
            log.info("memory_delete: deleted %s", raw)
            return f"OK: deleted {raw}"

        return f"Error: unknown builtin tool: {name}"

    except Exception as exc:
        log.error("Builtin tool %s failed: %s", name, exc)
        return f"Error: {exc}"
