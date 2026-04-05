# Implementation Plan: Claude Agent Platform

**Created:** 2026-04-04  
**Repository:** agents/  
**Namespace:** agents  

---

## Overview

Build a Kubernetes-native platform for running autonomous Claude agents as CronJobs. The primary deliverable is a daily personal briefing email synthesizing calendar events, Gmail, iMessages, Reminders, and Home Assistant sensor data. The platform is designed to be extensible — new agents are added by dropping an `AGENT.md` file into a ConfigMap.

---

## Phase 1: Infrastructure Foundation

**Goal:** Establish the Kubernetes namespace, RBAC, and the base CronJob framework. No agent logic yet — just the scaffolding.

### Step 1.1 — Namespace

Create the `agents` namespace following the same pattern as `bhavana/k8s/mcp/namespace.yaml`.

**File:** `k8s/agents/namespace.yaml`

```bash
kubectl apply -f k8s/agents/namespace.yaml
```

### Step 1.2 — RBAC: ServiceAccount with Read-Only Access

Create a `ServiceAccount` named `agent-runner` with read-only cluster access. The agent should never modify cluster state.

**Files:** `k8s/agents/rbac/`
- `serviceaccount.yaml` — `agent-runner` ServiceAccount in `agents` namespace
- `role.yaml` — Allows `get`, `list`, `watch` on ConfigMaps and Secrets within `agents` namespace (so the agent can read its own config)
- `clusterrole.yaml` — (Optional) Read-only access to cluster-scoped resources if needed later
- `rolebinding.yaml` — Binds role to `agent-runner` ServiceAccount

```bash
kubectl apply -f k8s/agents/rbac/
```

### Step 1.3 — Secret Scaffolding

Create the Kubernetes Secrets required by the agent runner. These are never in git — use `kubectl create secret`.

**Required secrets:**
- `anthropic-api-key` — Anthropic API key for Claude
- `gmail-oauth-credentials` — OAuth2 JSON for Gmail API
- `gcal-oauth-credentials` — OAuth2 JSON for Google Calendar API
- `ha-mcp-token` — Home Assistant long-lived access token

See `docs/secrets.md` for exact `kubectl` commands.

### Step 1.4 — Verification

```bash
kubectl get namespace agents
kubectl get serviceaccount -n agents
kubectl get role,rolebinding -n agents
```

---

## Phase 2: Agent Runner Container

**Goal:** Build and push the container that reads `AGENT.md`, connects to MCP servers, calls the Claude API in an agentic loop, and exits cleanly.

### Step 2.1 — Python Runner Script

**File:** `runner/run_agent.py`

The runner:
1. Reads `/config/AGENT.md` from a mounted ConfigMap volume
2. Reads `/config/mcp.json` from a mounted ConfigMap volume
3. Connects to each MCP server via HTTP/SSE, discovers tools
4. Submits the `AGENT.md` content as the user prompt to Claude API
5. Handles the agentic loop: `tool_use` → call MCP tool → `tool_result` → repeat
6. Exits on `end_turn` or when `MAX_TURNS` is reached
7. Logs all tool calls and final output

Uses the Anthropic Python SDK (`anthropic`) and the MCP Python client (`mcp`).

### Step 2.2 — Dockerfile

**File:** `runner/Dockerfile`

```
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY run_agent.py .
CMD ["python", "run_agent.py"]
```

### Step 2.3 — Build and Push

```bash
cd runner/
docker build -t csdread/agent-runner:1 .
docker push csdread/agent-runner:1
```

Update the image tag in `k8s/agents/daily-briefing/cronjob.yaml`.

### Step 2.4 — Local Test

Before deploying to k8s, test the runner locally with a minimal AGENT.md:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export AGENT_MD_PATH=./test-prompt.md
export MCP_CONFIG_PATH=./test-mcp.json
python runner/run_agent.py
```

---

## Phase 3: CronJob Deployment

**Goal:** Deploy the daily briefing CronJob with AGENT.md and MCP config loaded from ConfigMaps.

### Step 3.1 — AGENT.md ConfigMap

**File:** `k8s/agents/daily-briefing/configmap.yaml`

Contains two keys:
- `AGENT.md` — The agent prompt (see `prompts/daily-briefing/AGENT.md`)
- `mcp.json` — MCP server connection config

The ConfigMap mounts both files into the CronJob pod at `/config/`.

To update the prompt without rebuilding the image:
```bash
kubectl create configmap daily-briefing-config \
  --from-file=AGENT.md=prompts/daily-briefing/AGENT.md \
  --from-file=mcp.json=k8s/agents/daily-briefing/mcp.json \
  -n agents \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Step 3.2 — CronJob Manifest

**File:** `k8s/agents/daily-briefing/cronjob.yaml`

Key configuration:
- `schedule: "0 5 * * *"` with `timeZone: "America/Denver"` → runs at 5:00am MT daily
- `serviceAccountName: agent-runner` → read-only RBAC
- `restartPolicy: Never` → job fails fast, doesn't retry indefinitely
- `backoffLimit: 1` → one retry on failure
- `activeDeadlineSeconds: 1800` → hard 30-minute timeout
- Environment variables pull `ANTHROPIC_API_KEY` from Secret

### Step 3.3 — Deploy and Test

```bash
# Deploy
kubectl apply -f k8s/agents/daily-briefing/

# Verify CronJob created
kubectl get cronjob -n agents

# Manually trigger
kubectl create job -n agents --from=cronjob/daily-briefing daily-briefing-test

# Watch logs
kubectl logs -n agents -f job/daily-briefing-test
```

---

## Phase 4: Google Services MCP Servers

**Goal:** Deploy Gmail and Google Calendar MCP servers in the `agents` namespace so the agent can read email and calendar data.

### Step 4.1 — Google Cloud OAuth Setup

1. Go to Google Cloud Console → Create or select a project
2. Enable **Gmail API** and **Google Calendar API**
3. Create OAuth 2.0 credentials (Desktop App type)
4. Download `credentials.json`
5. Run the OAuth flow once locally to generate `token.json`:
   ```bash
   cd mcps/gmail/
   python authorize.py  # interactive browser-based OAuth
   ```
6. Store both as Kubernetes Secrets:
   ```bash
   kubectl create secret generic gmail-oauth \
     --from-file=credentials.json \
     --from-file=token.json \
     -n agents
   ```
7. Repeat for Calendar (same project, same credentials if scopes are combined)

> **Important:** Use read-only OAuth scopes:
> - Gmail: `https://www.googleapis.com/auth/gmail.readonly` + `https://www.googleapis.com/auth/gmail.send` (send needed for briefing email)
> - Calendar: `https://www.googleapis.com/auth/calendar.readonly`

### Step 4.2 — Gmail MCP Server

**Files:** `mcps/gmail/`

Python MCP server exposing tools:
- `gmail_search` — Search messages with a query string
- `gmail_read_message` — Read a message by ID
- `gmail_list_labels` — List available labels
- `gmail_send` — Send an email (used by agent to deliver the briefing)

**Deployment:**
```bash
cd mcps/gmail/
docker build -t csdread/gmail-mcp:1 .
docker push csdread/gmail-mcp:1
kubectl apply -f k8s/agents/gmail-mcp/
```

### Step 4.3 — Google Calendar MCP Server

**Files:** `mcps/gcal/`

Python MCP server exposing tools:
- `gcal_list_events` — List events for a date range
- `gcal_get_event` — Get a specific event by ID
- `gcal_list_calendars` — List available calendars

**Deployment:**
```bash
cd mcps/gcal/
docker build -t csdread/gcal-mcp:1 .
docker push csdread/gcal-mcp:1
kubectl apply -f k8s/agents/gcal-mcp/
```

### Step 4.4 — MCP Config Update

Update the `mcp.json` in the `daily-briefing` ConfigMap to include the new servers:

```json
{
  "mcpServers": {
    "home-assistant": {
      "url": "http://ha-mcp.mcp.svc.cluster.local:8086/sse"
    },
    "gmail": {
      "url": "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
    },
    "google-calendar": {
      "url": "http://gcal-mcp.agents.svc.cluster.local:3001/sse"
    }
  }
}
```

---

## Phase 5: Mac Mini Bridge

**Goal:** Set up the Mac mini to expose iMessages and Reminders as an MCP server accessible from the Kubernetes cluster.

### Overview

The Mac mini serves as a bridge for data sources that are only available on Apple hardware:
- **iMessages** — Read via AppleScript or `Messages.app` shortcuts
- **Reminders** — Read via AppleScript or `EventKit` framework (via Python `pyobjc`)

The bridge runs as a persistent background service (`launchd`) on the Mac mini and exposes an HTTP/SSE MCP server on port 4000.

### Step 5.1 — Mac Mini Prerequisites

The Mac mini should:
- Be on the local network (static IP or reserved DHCP, e.g., `192.168.1.200`)
- Have iCloud signed in (for iMessages and Reminders)
- Have Python 3.12+ installed (via Homebrew)
- Have Messages.app and Reminders.app open and authorized

### Step 5.2 — Bridge MCP Server

**Files:** `mcps/mac-bridge/` *(to be created in Phase 5)*

Python MCP server on the Mac mini exposing:
- `messages_list_unread` — List unread iMessage conversations
- `messages_get_conversation` — Get messages in a conversation
- `reminders_list` — List all reminders (with due dates, completion status)
- `reminders_get_list` — Get reminders in a specific list

Uses:
- `pyobjc` for native Reminders access via EventKit
- AppleScript (via `subprocess`) for iMessages

### Step 5.3 — launchd Service

Install the MCP server as a launchd service so it starts on boot:

```bash
# On the Mac mini
cd ~/agents-bridge/
pip install -r requirements.txt

# Install launchd plist
cp com.agents.mac-bridge.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.agents.mac-bridge.plist
```

### Step 5.4 — Kubernetes ExternalName Service

**File:** `k8s/agents/mac-bridge/service.yaml`

Creates a Kubernetes Service that resolves to the Mac mini's IP:

```yaml
kind: Service
spec:
  type: ExternalName
  externalName: 192.168.1.200  # Mac mini IP (use actual static IP)
```

This allows in-cluster pods to connect to `mac-bridge.agents.svc.cluster.local:4000`.

### Step 5.5 — Update MCP Config

Add to `mcp.json`:
```json
"mac-bridge": {
  "url": "http://mac-bridge.agents.svc.cluster.local:4000/sse"
}
```

---

## Phase 6: Daily Briefing Agent Polish

**Goal:** Tune the AGENT.md prompt and validate end-to-end quality of the daily briefing email.

### Step 6.1 — Prompt Refinement

**File:** `prompts/daily-briefing/AGENT.md`

Iterate on the prompt based on actual output quality:
- Adjust section priorities based on what's most useful
- Add date/time context injection (the CronJob can pass today's date as an env var)
- Tune email formatting (HTML vs. plain text)

### Step 6.2 — Home Assistant Integration

The Home Assistant MCP server at `ha-mcp.mcp.svc.cluster.local:8086` provides sensor data. Configure the prompt to query:

- **Robot vacuum** — Last run time, battery, error states, schedule
  - Entity pattern: `vacuum.*`
- **Hot tub** — Temperature, target temp, filter due, water care schedule
  - Entity pattern: `sensor.hot_tub_*` or `climate.hot_tub`
- **Maintenance automations** — Any automations flagged for manual action
  - Entity pattern: `input_boolean.maintenance_*`
- **Indoor/outdoor temperature** — Weather context for the day
- **Security** — Any alerts from the overnight period

The agent can use `ha_get_states` or `ha_call_service` (read-only tools only) to gather this data.

### Step 6.3 — Email Template Validation

The agent sends the briefing via the Gmail MCP `gmail_send` tool. Validate:
- Email arrives in inbox
- Formatting renders correctly in Gmail and Apple Mail
- Subject line includes the date
- All sections populated correctly

### Step 6.4 — Error Handling

- If an MCP server is down, the agent should note it and continue with available sources
- Add `BRIEFING_EMAIL` env var to the CronJob for the destination address
- Consider adding a Slack/ntfy notification on job failure

---

## Phase 7: Hardening and Operations

**Goal:** Make the platform production-ready for daily unattended operation.

### Step 7.1 — Image Versioning

- Pin all image tags (no `latest` in production)
- Create a `Makefile` with build and push targets
- Document the image update process

### Step 7.2 — Secret Rotation

- Document how to rotate the Anthropic API key
- Document how to re-authorize Google OAuth tokens (expire after some period)
- Set calendar reminders for token expiry

### Step 7.3 — Observability

- Add Prometheus `ServiceMonitor` for job success/failure metrics (matches pattern in `bhavana/k8s/monitoring/`)
- Consider ntfy.sh or Pushover notification on job failure
- Keep 3 days of completed Job objects (`successfulJobsHistoryLimit: 3`)

### Step 7.4 — Network Policies

Add NetworkPolicy resources to restrict egress from agent pods:
- Allow egress to MCP servers (by ClusterIP/namespace)
- Allow egress to Anthropic API (HTTPS to api.anthropic.com)
- Allow egress to Google APIs (for MCP servers)
- Deny all other egress

### Step 7.5 — Resource Limits

Tune resource requests/limits based on observed usage:
- The agent runner is CPU-light but may need more memory for long runs
- MCP servers are lightweight services

---

## Timeline Summary

| Phase | Description | Effort | Prerequisites |
|-------|-------------|--------|---------------|
| 1 | Infrastructure Foundation | Low | kubectl access |
| 2 | Agent Runner Container | Medium | Docker, Anthropic API key |
| 3 | CronJob Deployment | Low | Phase 1, 2 |
| 4 | Google Services MCPs | High | Google Cloud project, OAuth |
| 5 | Mac Mini Bridge | High | Mac mini, iCloud |
| 6 | Daily Briefing Polish | Medium | Phase 3, 4, 5 |
| 7 | Hardening & Operations | Medium | Phase 6 |

---

## Open Questions

- **Mac mini static IP:** Confirm IP address before deploying the ExternalName service
- **Registry:** Confirm `csdread/` Docker Hub registry is accessible from the cluster (or switch to `ghcr.io`)
- **Google OAuth scopes:** Decide if the agent should only read Gmail or also send (for the briefing itself)
- **Email destination:** Configure `BRIEFING_EMAIL` env var in CronJob
- **HA entity names:** Confirm exact entity IDs for vacuum, hot tub, and maintenance sensors in Home Assistant
- **Token refresh:** Google OAuth refresh tokens can expire if unused for 6 months — plan for periodic re-auth
