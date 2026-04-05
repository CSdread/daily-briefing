#!/usr/bin/env python3
"""
Gmail MCP Server

Exposes Gmail read and send operations as MCP tools over HTTP/SSE.
OAuth credentials are loaded from /oauth/credentials.json and /oauth/token.json
which are mounted from a Kubernetes Secret.

Tools exposed:
  gmail_search          - Search messages with a query string
  gmail_read_message    - Read a message by ID (returns subject, from, body snippet)
  gmail_list_labels     - List all labels in the mailbox
  gmail_send            - Send an email message

Environment variables:
  PORT    HTTP port to listen on. Default: 3000
  OAUTH_DIR  Directory containing credentials.json and token.json. Default: /oauth
"""

import json
import os
import base64
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
import mcp.types as types

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

PORT = int(os.environ.get("PORT", "3000"))
OAUTH_DIR = Path(os.environ.get("OAUTH_DIR", "/oauth"))

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
]


def get_gmail_service():
    credentials_path = OAUTH_DIR / "credentials.json"
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
                "Gmail credentials invalid or missing. "
                "Run authorize.py locally and update the gmail-oauth secret."
            )

    return build("gmail", "v1", credentials=creds)


def extract_body(payload: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    # Fallback: first text part found recursively
    for part in payload.get("parts", []):
        result = extract_body(part)
        if result:
            return result

    return ""


# MCP Server setup
server = Server("gmail-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="gmail_search",
            description=(
                "Search Gmail messages. Returns a list of matching messages with "
                "id, subject, from, date, and snippet. "
                "Use Gmail search syntax: 'is:unread', 'from:someone@example.com', "
                "'after:2024/1/1', 'subject:meeting', etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query string",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return. Default: 20",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="gmail_read_message",
            description="Read the full content of a Gmail message by its ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "Gmail message ID (from gmail_search results)",
                    },
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="gmail_list_labels",
            description="List all Gmail labels (folders/categories) in the mailbox.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="gmail_send",
            description="Send an email via Gmail.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text or HTML)",
                    },
                    "html": {
                        "type": "boolean",
                        "description": "True if body is HTML. Default: false",
                        "default": False,
                    },
                },
                "required": ["to", "subject", "body"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    service = get_gmail_service()

    if name == "gmail_search":
        query = arguments["query"]
        max_results = arguments.get("max_results", 20)

        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return [TextContent(type="text", text="No messages found matching the query.")]

        output_lines = [f"Found {len(messages)} message(s):\n"]
        for msg in messages:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            output_lines.append(
                f"ID: {msg['id']}\n"
                f"  From: {headers.get('From', 'Unknown')}\n"
                f"  Subject: {headers.get('Subject', '(no subject)')}\n"
                f"  Date: {headers.get('Date', 'Unknown')}\n"
                f"  Snippet: {detail.get('snippet', '')[:150]}\n"
            )

        return [TextContent(type="text", text="\n".join(output_lines))]

    elif name == "gmail_read_message":
        message_id = arguments["message_id"]

        detail = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        body = extract_body(detail.get("payload", {}))

        output = (
            f"From: {headers.get('From', 'Unknown')}\n"
            f"To: {headers.get('To', 'Unknown')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Date: {headers.get('Date', 'Unknown')}\n"
            f"---\n"
            f"{body[:3000]}"
        )
        if len(body) > 3000:
            output += f"\n... (truncated, {len(body)} chars total)"

        return [TextContent(type="text", text=output)]

    elif name == "gmail_list_labels":
        result = service.users().labels().list(userId="me").execute()
        labels = result.get("labels", [])
        lines = [f"Labels ({len(labels)} total):\n"]
        for label in sorted(labels, key=lambda x: x["name"]):
            lines.append(f"  {label['name']} (id: {label['id']})")
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "gmail_send":
        to = arguments["to"]
        subject = arguments["subject"]
        body = arguments["body"]
        is_html = arguments.get("html", False)

        mime_type = "html" if is_html else "plain"
        msg = MIMEMultipart()
        msg["to"] = to
        msg["subject"] = subject
        msg.attach(MIMEText(body, mime_type))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        return [TextContent(type="text", text=f"Email sent successfully to {to}")]

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
    return JSONResponse({"status": "ok", "server": "gmail-mcp"})


app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/message", app=sse_transport.handle_post_message),
        Route("/health", endpoint=handle_health),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
