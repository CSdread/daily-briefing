#!/usr/bin/env python3
"""
Mac Bridge MCP Server

Exposes iMessages and Reminders as MCP tools over HTTP/SSE.

iMessages: read via SQLite (~/Library/Messages/chat.db)
  - Native: direct file access
  - Container: hostPath volume mount of the Messages directory

Reminders: read via iCloud CalDAV
  - Works identically in both native and containerized modes
  - Requires ICLOUD_APPLE_ID and ICLOUD_APP_PASSWORD env vars
  - Use an app-specific password from appleid.apple.com (not your Apple ID password)

Tools exposed:
  messages_list_conversations  - List conversations with unread counts
  messages_get_unread          - Get unread messages across all conversations
  messages_get_conversation    - Get recent messages in a specific conversation
  reminders_list_all           - List all reminders across all lists
  reminders_get_incomplete     - Get only incomplete reminders
  reminders_get_by_list        - Get reminders in a specific list
  reminders_get_due_today      - Get reminders due today or overdue

Environment variables:
  PORT                HTTP port. Default: 4000
  MESSAGES_DB         Path to chat.db. Default: ~/Library/Messages/chat.db
  ICLOUD_APPLE_ID     Apple ID email address
  ICLOUD_APP_PASSWORD App-specific password (from appleid.apple.com)
  REMINDERS_CACHE_TTL Seconds to cache reminder fetches. Default: 300
"""

import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import caldav
from icalendar import Calendar
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
import uvicorn

PORT = int(os.environ.get("PORT", "4000"))
MESSAGES_DB = Path(os.environ.get("MESSAGES_DB", os.path.expanduser("~/Library/Messages/chat.db")))
ICLOUD_APPLE_ID = os.environ.get("ICLOUD_APPLE_ID", "")
ICLOUD_APP_PASSWORD = os.environ.get("ICLOUD_APP_PASSWORD", "")
REMINDERS_CACHE_TTL = int(os.environ.get("REMINDERS_CACHE_TTL", "300"))

# Apple's epoch starts 2001-01-01; timestamps are in nanoseconds
_APPLE_EPOCH_OFFSET = 978307200


# ── iMessages (SQLite) ────────────────────────────────────────────────────────

def _messages_conn() -> sqlite3.Connection:
    if not MESSAGES_DB.exists():
        raise RuntimeError(
            f"Messages database not found at {MESSAGES_DB}. "
            "Grant Full Disk Access to the Python process in System Settings → Privacy & Security."
        )
    # Open read-only to avoid corrupting the live database
    return sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)


def _apple_ts_to_str(ts: int) -> str:
    try:
        dt = datetime.fromtimestamp(ts / 1_000_000_000 + _APPLE_EPOCH_OFFSET)
        return dt.strftime("%b %-d %-I:%M %p")
    except Exception:
        return "Unknown"


def messages_list_conversations_impl(limit: int = 30) -> str:
    conn = _messages_conn()
    try:
        rows = conn.execute("""
            SELECT
                c.chat_identifier,
                c.display_name,
                COUNT(CASE WHEN m.is_read = 0 AND m.is_from_me = 0 THEN 1 END) AS unread_count,
                MAX(m.date) AS last_ts
            FROM chat c
            JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            JOIN message m ON cmj.message_id = m.ROWID
            GROUP BY c.ROWID
            ORDER BY last_ts DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    if not rows:
        return "No conversations found."

    lines = [f"Conversations ({len(rows)}):\n"]
    for identifier, display_name, unread, last_ts in rows:
        name = display_name or identifier
        unread_str = f" [{unread} unread]" if unread else ""
        lines.append(f"  {name}{unread_str} — last message {_apple_ts_to_str(last_ts)}")
    return "\n".join(lines)


def messages_get_unread_impl(limit: int = 50) -> str:
    conn = _messages_conn()
    try:
        rows = conn.execute("""
            SELECT
                h.id AS sender,
                c.display_name,
                c.chat_identifier,
                m.text,
                m.date
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE m.is_read = 0
              AND m.is_from_me = 0
              AND m.text IS NOT NULL
            ORDER BY m.date DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    if not rows:
        return "No unread messages."

    lines = [f"Unread messages ({len(rows)}):\n"]
    for sender, display_name, identifier, text, ts in rows:
        chat_name = display_name or identifier or sender
        snippet = (text or "")[:200]
        lines.append(
            f"  From: {sender} (chat: {chat_name})\n"
            f"  Time: {_apple_ts_to_str(ts)}\n"
            f"  Message: {snippet}\n"
        )
    return "\n".join(lines)


def messages_get_conversation_impl(handle: str, limit: int = 20) -> str:
    conn = _messages_conn()
    try:
        rows = conn.execute("""
            SELECT
                m.is_from_me,
                h.id AS sender_handle,
                m.text,
                m.date
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE (c.chat_identifier = ? OR c.display_name = ?)
              AND m.text IS NOT NULL
            ORDER BY m.date DESC
            LIMIT ?
        """, (handle, handle, limit)).fetchall()
    finally:
        conn.close()

    if not rows:
        return f"No messages found for conversation: {handle}"

    lines = [f"Conversation: {handle} (last {len(rows)} messages, newest first)\n"]
    for is_from_me, sender, text, ts in rows:
        who = "Me" if is_from_me else (sender or "Them")
        snippet = (text or "")[:300]
        lines.append(f"  [{_apple_ts_to_str(ts)}] {who}: {snippet}")
    return "\n".join(lines)


# ── Reminders (iCloud CalDAV) ─────────────────────────────────────────────────

_reminders_cache: dict = {}


def _fetch_all_reminders() -> list[dict]:
    """Fetch all reminders from iCloud CalDAV with TTL caching."""
    now = time.time()
    if _reminders_cache.get("data") and now - _reminders_cache.get("ts", 0) < REMINDERS_CACHE_TTL:
        return _reminders_cache["data"]

    if not ICLOUD_APPLE_ID or not ICLOUD_APP_PASSWORD:
        raise RuntimeError(
            "ICLOUD_APPLE_ID and ICLOUD_APP_PASSWORD are required for Reminders. "
            "Generate an app-specific password at appleid.apple.com."
        )

    client = caldav.DAVClient(
        url="https://caldav.icloud.com",
        username=ICLOUD_APPLE_ID,
        password=ICLOUD_APP_PASSWORD,
    )
    principal = client.principal()
    calendars = principal.calendars()

    reminders: list[dict] = []
    for cal in calendars:
        try:
            todos = cal.todos(include_completed=True)
        except Exception:
            # Calendar doesn't support VTODO (it's an event calendar) — skip
            continue

        cal_name = cal.name or "Unknown List"
        for todo in todos:
            comp = todo.icalendar_component
            name = str(comp.get("SUMMARY", "")).strip()
            if not name:
                continue

            status = str(comp.get("STATUS", "NEEDS-ACTION")).upper()
            completed = status == "COMPLETED"

            due_val = comp.get("DUE")
            due_date: date | None = None
            due_str = ""
            if due_val:
                dt = due_val.dt
                due_date = dt.date() if isinstance(dt, datetime) else dt
                due_str = due_date.strftime("%Y-%m-%d")

            description = str(comp.get("DESCRIPTION", "")).strip()

            reminders.append({
                "list": cal_name,
                "name": name,
                "completed": completed,
                "due": due_str,
                "due_date": due_date,
                "description": description,
            })

    _reminders_cache["data"] = reminders
    _reminders_cache["ts"] = now
    return reminders


def _format_reminder(r: dict) -> str:
    due = f" (due: {r['due']})" if r["due"] else ""
    desc = f"\n    Note: {r['description']}" if r["description"] else ""
    return f"  [{r['list']}] {r['name']}{due}{desc}"


def reminders_list_all_impl() -> str:
    reminders = _fetch_all_reminders()
    if not reminders:
        return "No reminders found."
    lines = [f"All reminders ({len(reminders)}):\n"]
    for r in reminders:
        status = "✓" if r["completed"] else "○"
        lines.append(f"  {status}{_format_reminder(r).strip()}")
    return "\n".join(lines)


def reminders_get_incomplete_impl() -> str:
    reminders = [r for r in _fetch_all_reminders() if not r["completed"]]
    if not reminders:
        return "No incomplete reminders."
    lines = [f"Incomplete reminders ({len(reminders)}):\n"]
    for r in reminders:
        lines.append(_format_reminder(r))
    return "\n".join(lines)


def reminders_get_by_list_impl(list_name: str) -> str:
    reminders = [r for r in _fetch_all_reminders() if r["list"].lower() == list_name.lower()]
    if not reminders:
        return f"No reminders found in list: {list_name}"
    lines = [f"Reminders in '{list_name}' ({len(reminders)}):\n"]
    for r in reminders:
        status = "✓" if r["completed"] else "○"
        lines.append(f"  {status} {r['name']}" + (f" (due: {r['due']})" if r["due"] else ""))
    return "\n".join(lines)


def reminders_get_due_today_impl() -> str:
    today = date.today()
    reminders = [
        r for r in _fetch_all_reminders()
        if not r["completed"] and r["due_date"] is not None and r["due_date"] <= today
    ]
    if not reminders:
        return "No reminders due today or overdue."
    lines = [f"Due today / overdue ({len(reminders)}):\n"]
    for r in reminders:
        overdue = "  ⚠ OVERDUE" if r["due_date"] < today else ""
        lines.append(f"{_format_reminder(r)}{overdue}")
    return "\n".join(lines)


# ── MCP Server ────────────────────────────────────────────────────────────────

server = Server("mac-bridge")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="messages_list_conversations",
            description="List iMessage conversations with unread counts and last message time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max conversations to return. Default: 30",
                        "default": 30,
                    },
                },
            },
        ),
        Tool(
            name="messages_get_unread",
            description="Get all unread iMessages across all conversations, newest first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max messages to return. Default: 50",
                        "default": 50,
                    },
                },
            },
        ),
        Tool(
            name="messages_get_conversation",
            description=(
                "Get recent messages in a specific iMessage conversation. "
                "Pass the chat_identifier (phone number, email) or display name "
                "from messages_list_conversations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "Phone number, email address, or display name of the conversation",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max messages to return. Default: 20",
                        "default": 20,
                    },
                },
                "required": ["handle"],
            },
        ),
        Tool(
            name="reminders_list_all",
            description="List all Reminders (complete and incomplete) across all iCloud lists.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="reminders_get_incomplete",
            description="Get only incomplete (not yet done) Reminders across all iCloud lists.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="reminders_get_by_list",
            description="Get all Reminders in a specific iCloud list (e.g., 'Home', 'Work', 'Groceries').",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the Reminders list",
                    },
                },
                "required": ["list_name"],
            },
        ),
        Tool(
            name="reminders_get_due_today",
            description="Get incomplete Reminders that are due today or overdue.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "messages_list_conversations":
            result = messages_list_conversations_impl(limit=arguments.get("limit", 30))
        elif name == "messages_get_unread":
            result = messages_get_unread_impl(limit=arguments.get("limit", 50))
        elif name == "messages_get_conversation":
            result = messages_get_conversation_impl(
                handle=arguments["handle"],
                limit=arguments.get("limit", 20),
            )
        elif name == "reminders_list_all":
            result = reminders_list_all_impl()
        elif name == "reminders_get_incomplete":
            result = reminders_get_incomplete_impl()
        elif name == "reminders_get_by_list":
            result = reminders_get_by_list_impl(list_name=arguments["list_name"])
        elif name == "reminders_get_due_today":
            result = reminders_get_due_today_impl()
        else:
            result = f"Unknown tool: {name}"
    except Exception as exc:
        result = f"Error: {exc}"

    return [TextContent(type="text", text=result)]


# ── HTTP / SSE transport ──────────────────────────────────────────────────────

sse_transport = SseServerTransport("/message")


async def handle_sse_mount(scope, receive, send):
    # Mounted at /sse — Starlette strips that prefix before calling us, so:
    #   GET  /sse  or /sse/   → scope["path"] == "/" → open SSE connection
    #   POST /sse/message?... → scope["path"] == "/message" → forward to message handler
    #
    # mcp 1.x sends the message endpoint as a relative URL ("message"), which
    # the client resolves against the SSE base URL (/sse/) → /sse/message.
    # Both cases must be handled inside this single mount.
    if scope.get("path", "/").startswith("/message"):
        await sse_transport.handle_post_message(scope, receive, send)
    else:
        async with sse_transport.connect_sse(scope, receive, send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())


async def handle_health(request):
    return JSONResponse({"status": "ok", "server": "mac-bridge"})


app = Starlette(
    routes=[
        Mount("/sse", app=handle_sse_mount),
        Route("/health", endpoint=handle_health),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
