#!/usr/bin/env python3
"""
Google Calendar MCP Server

Exposes Google Calendar read operations as MCP tools over HTTP/SSE.
OAuth credentials are loaded from /oauth/credentials.json and /oauth/token.json
mounted from a Kubernetes Secret.

Tools exposed:
  gcal_list_calendars   - List all calendars the user has access to
  gcal_list_events      - List events within a date range
  gcal_get_event        - Get a specific event by ID

Environment variables:
  PORT      HTTP port to listen on. Default: 3001
  OAUTH_DIR Directory containing credentials.json and token.json. Default: /oauth
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MOUNTAIN = ZoneInfo("America/Denver")

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

PORT = int(os.environ.get("PORT", "3001"))
OAUTH_DIR = Path(os.environ.get("OAUTH_DIR", "/oauth"))

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
]


def get_calendar_service():
    token_path = OAUTH_DIR / "token.json"

    creds = None
    if token_path.exists():
        token_data = json.loads(token_path.read_text())
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", SCOPES),
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "Google Calendar credentials invalid or missing. "
                "Run authorize.py locally and update the gcal-oauth secret."
            )

    return build("calendar", "v3", credentials=creds)


def format_event(event: dict) -> str:
    """Format a calendar event for display."""
    start = event.get("start", {})
    end = event.get("end", {})

    start_raw = start.get("dateTime", start.get("date", "Unknown"))
    end_raw = end.get("dateTime", end.get("date", "Unknown"))

    # Convert to Mountain Time and include the full date so the caller
    # can unambiguously assign events to the correct local day.
    # Late-evening MT events cross midnight UTC — showing only the time
    # caused misassignment when the agent grouped by UTC date.
    if "T" in start_raw:
        try:
            dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(MOUNTAIN)
            start_str = dt.strftime("%Y-%m-%d %-I:%M %p MT")
        except Exception:
            start_str = start_raw
    else:
        start_str = start_raw  # all-day event, already a date string

    if "T" in end_raw:
        try:
            dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(MOUNTAIN)
            end_str = dt.strftime("%-I:%M %p MT")
        except Exception:
            end_str = end_raw
    else:
        end_str = end_raw

    attendees = event.get("attendees", [])
    attendee_str = ""
    if attendees:
        names = [a.get("displayName", a.get("email", "Unknown")) for a in attendees[:5]]
        attendee_str = f"\n  Attendees: {', '.join(names)}"
        if len(attendees) > 5:
            attendee_str += f" (+{len(attendees)-5} more)"

    location = event.get("location", "")
    location_str = f"\n  Location: {location}" if location else ""

    description = event.get("description", "")
    description_str = f"\n  Description: {description[:200]}" if description else ""

    return (
        f"ID: {event.get('id', 'Unknown')}\n"
        f"  Title: {event.get('summary', '(no title)')}\n"
        f"  Time: {start_str} – {end_str}"
        f"{location_str}"
        f"{attendee_str}"
        f"{description_str}"
    )


# MCP Server setup
server = Server("gcal-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="gcal_list_calendars",
            description="List all Google Calendars the user has access to.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="gcal_list_events",
            description=(
                "List Google Calendar events within a date range. "
                "Returns event title, time, location, attendees, and description. "
                "Defaults to today if no dates provided."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID. Use 'primary' for the main calendar. Default: 'primary'",
                        "default": "primary",
                    },
                    "time_min": {
                        "type": "string",
                        "description": "Start of date range (ISO 8601 format, e.g. '2026-04-05T00:00:00-07:00')",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of date range (ISO 8601 format)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum events to return. Default: 20",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="gcal_get_event",
            description="Get full details of a specific calendar event by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID. Default: 'primary'",
                        "default": "primary",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Event ID (from gcal_list_events results)",
                    },
                },
                "required": ["event_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    service = get_calendar_service()

    if name == "gcal_list_calendars":
        result = service.calendarList().list().execute()
        calendars = result.get("items", [])
        lines = [f"Calendars ({len(calendars)} total):\n"]
        for cal in sorted(calendars, key=lambda x: x.get("summary", "")):
            primary = " [PRIMARY]" if cal.get("primary") else ""
            lines.append(f"  {cal.get('summary', 'Unnamed')}{primary} (id: {cal['id']})")
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "gcal_list_events":
        calendar_id = arguments.get("calendar_id", "primary")
        max_results = arguments.get("max_results", 20)

        now = datetime.now(timezone.utc)

        time_min = arguments.get("time_min")
        time_max = arguments.get("time_max")

        if not time_min:
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if not time_max:
            time_max = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = result.get("items", [])
        if not events:
            return [TextContent(type="text", text="No events found in this date range.")]

        lines = [f"Events ({len(events)}):\n"]
        for event in events:
            lines.append(format_event(event))
            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "gcal_get_event":
        calendar_id = arguments.get("calendar_id", "primary")
        event_id = arguments["event_id"]

        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()

        return [TextContent(type="text", text=format_event(event))]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# HTTP/SSE transport setup
sse_transport = SseServerTransport("/message")


async def handle_sse(request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def handle_health(request):
    return JSONResponse({"status": "ok", "server": "gcal-mcp"})


app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/message", app=sse_transport.handle_post_message),
        Route("/health", endpoint=handle_health),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
