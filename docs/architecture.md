# System Architecture

## Overview

The agents platform runs autonomous Claude agents as Kubernetes CronJobs. Each agent is defined entirely by an `AGENT.md` prompt file stored in a ConfigMap. The agent runner container handles the Claude API loop and MCP tool proxying.

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Kubernetes Cluster                               │
│                                                                           │
│  ┌────────────────────────── agents namespace ─────────────────────────┐ │
│  │                                                                       │ │
│  │  ┌──────────────────────────────────────────────────────────────┐   │ │
│  │  │                CronJob: daily-briefing                        │   │ │
│  │  │               schedule: 0 5 * * * (MT)                       │   │ │
│  │  │                                                               │   │ │
│  │  │  ┌───────────────────────────────────────────────────────┐  │   │ │
│  │  │  │                  agent-runner container                │  │   │ │
│  │  │  │                                                        │  │   │ │
│  │  │  │   /config/AGENT.md ──► Claude Agentic Loop            │  │   │ │
│  │  │  │   /config/mcp.json       │                            │  │   │ │
│  │  │  │   /memory/ ◄────────────►│ (read before, write after) │  │   │ │
│  │  │  │                          ▼                            │  │   │ │
│  │  │  │              Anthropic API (api.anthropic.com)        │  │   │ │
│  │  │  │                          │                            │  │   │ │
│  │  │  │              tool_use ◄──┘                            │  │   │ │
│  │  │  │                 │                                     │  │   │ │
│  │  │  │          MCP Client (HTTP/SSE)                        │  │   │ │
│  │  │  └───────────────────────────────────────────────────────┘  │   │ │
│  │  └──────────────────────────────────────────────────────────────┘   │ │
│  │             │              │              │             │            │ │
│  │             ▼              ▼              ▼             ▼            │ │
│  │    ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────┐ │ │
│  │    │  Gmail MCP   │ │ GCal MCP │ │mac-bridge│ │  (ExternalName) │ │ │
│  │    │  :3000/sse   │ │ :3001/sse│ │ExternalName│ │  → Mac mini     │ │ │
│  │    │  ClusterIP   │ │ ClusterIP│ │:4000/sse │ │  192.168.1.200  │ │ │
│  │    └──────────────┘ └──────────┘ └──────────┘ └─────────────────┘ │ │
│  │                                                                       │ │
│  │  ┌──────────────────────────────────────────────────────────────┐   │ │
│  │  │  PVC: agent-daily-briefing-1  →  NFS: soma.bhavana.local     │   │ │
│  │  │  /kube-volumes/agent-daily-briefing-1  (mounted at /memory)  │   │ │
│  │  └──────────────────────────────────────────────────────────────┘   │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ┌─────────────────── mcp namespace ──────────────────────────────────┐ │
│  │  ┌──────────────────────────┐  ┌──────────────────────────────┐   │ │
│  │  │  Home Assistant MCP      │  │  GitHub MCP                  │   │ │
│  │  │  ha-mcp.mcp.svc:8086    │  │  github-mcp.mcp.svc:8082    │   │ │
│  │  └──────────────────────────┘  └──────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                │                    │                   │
                ▼                    ▼                   ▼
    ┌───────────────────┐  ┌─────────────────┐  ┌──────────────────────┐
    │   Anthropic API   │  │   Google APIs   │  │     Mac mini         │
    │ api.anthropic.com │  │  Gmail/Calendar │  │  192.168.1.200:4000  │
    │ claude-sonnet-4-6 │  │  OAuth2 tokens  │  │  - iMessage bridge   │
    └───────────────────┘  └─────────────────┘  │  - Reminders bridge  │
                                                 └──────────────────────┘
                                                          │
                                                          ▼
                                                 ┌──────────────────────┐
                                                 │  Home Assistant      │
                                                 │  192.168.1.26:8123   │
                                                 │  via ha-mcp          │
                                                 └──────────────────────┘
```

---

## Data Flow: Daily Briefing

```
5:00 AM MT
     │
     ▼
CronJob creates Pod
     │
     ▼
agent-runner starts
     │
     ├─ Mount: /config/AGENT.md (from ConfigMap)
     ├─ Mount: /config/mcp.json (from ConfigMap)
     ├─ Mount: /memory (from NFS PVC — agent memory)
     └─ Env: ANTHROPIC_API_KEY (from Secret)
     │
     ▼
Read /memory/index.md → confirm memory is available
     │
     ▼
Connect to MCP servers
     ├─ Gmail MCP (list tools)
     ├─ Google Calendar MCP (list tools)
     ├─ Home Assistant MCP (list tools)
     └─ Mac Bridge (list tools)
     │
     ▼
Submit AGENT.md prompt to Claude API
     │
     ▼ (agentic loop — Pass 1: read memory, then fetch)
     ├─ Read /memory/calendar_events/* → reuse stored event data if unchanged
     ├─ gcal_list_events → today + 2 days of calendar events
     ├─ Read /memory/email_threads/* → skip unchanged low-importance threads
     ├─ gmail_search → unread emails, pending responses
     ├─ Read /memory/people/* → enrich names with known relationships
     ├─ messages_list_unread → unread iMessages
     ├─ reminders_list → overdue + due today
     ├─ ha_get_states → vacuum, hot tub, sensors
     └─ ... (more tool calls as needed)
     │
     ▼
Claude composes briefing email (HTML)
     │
     ▼
gmail_send → sends email to BRIEFING_EMAIL
     │
     ▼ (Pass 2: write memory updates)
     ├─ Write /memory/calendar_events/* (append shown_on dates)
     ├─ Write /memory/email_threads/* (summaries, importance, prune old)
     ├─ Write /memory/people/* (new/updated relationship inferences)
     └─ Write /memory/escalations.json (increment counters, mark resolved)
     │
     ▼
Agent returns end_turn → Pod exits 0
```

---

## Agent Memory

The daily briefing agent uses a persistent filesystem-based memory store to reduce redundant work and accumulate context across runs.

### Storage

Memory is backed by an NFS PersistentVolume on `soma.bhavana.local` at `/kube-volumes/agent-daily-briefing-1`, mounted read-write at `/memory` inside the agent container. The root container filesystem remains read-only — `/memory` is the only writable mount.

### Memory Areas

| Path | Content | Purpose |
|------|---------|---------|
| `/memory/index.md` | Presence marker | Agent reads this to confirm memory is live; created on first run |
| `/memory/people/{slug}.json` | Name, aliases, email, relationship, notes | Enrich output with known relationships; avoid re-inferring each run |
| `/memory/email_threads/{thread_id}.json` | Summary, importance, timestamps, shown count | Skip re-reading unchanged threads; surface persistent action items |
| `/memory/calendar_events/{event_id}.json` | Event ID, dates shown | Keep event display consistent across the 3-day window |
| `/memory/escalations.json` | Unresolved flagged items with counters | Track items not actioned across multiple days |

### Two-Pass Pattern

1. **Before fetching:** read relevant memory files to skip redundant API calls and enrich names with known relationships.
2. **After sending:** batch all writes — never write before the email is sent.

Memory is optional. If `/memory/index.md` is unreadable (volume not mounted), the agent runs without it.

---

## RBAC Design

The `agent-runner` ServiceAccount has minimal, read-only permissions:

| Resource | Verbs | Scope |
|----------|-------|-------|
| configmaps | get, list, watch | agents namespace |
| pods | get, list, watch | agents namespace |
| jobs | get, list, watch | agents namespace |

No write access to any Kubernetes resources. MCP tools are the only way the agent interacts with external systems.

---

## ConfigMap-Driven Prompts

The key design principle: **the agent's behavior is entirely defined by `AGENT.md`** in a ConfigMap. No code changes needed to change what the agent does.

```
ConfigMap: daily-briefing-config
├── AGENT.md    → mounted at /config/AGENT.md
└── mcp.json    → mounted at /config/mcp.json
```

To change the agent's behavior:
```bash
kubectl patch configmap daily-briefing-config -n agents \
  --patch-file new-agent.yaml
# Next job run picks up the new prompt automatically
```

---

## MCP Server Architecture

All MCP servers use HTTP/SSE transport (not stdio) for compatibility with Kubernetes networking.

```
MCP Server (Python FastAPI)
├── GET /sse     → SSE stream for MCP protocol
├── POST /message → Client-to-server messages
└── GET /health  → Liveness probe
```

The agent runner acts as an MCP client:
1. Connects to each server's `/sse` endpoint
2. Receives tool definitions (list_tools)
3. For each Claude `tool_use` block: calls the server's `call_tool`
4. Returns `tool_result` to Claude

---

## Mac Mini Bridge

The Mac mini serves data that is only accessible on Apple hardware. It runs a Python MCP server that uses:

- **pyobjc + EventKit** for Reminders (native framework access)
- **AppleScript via subprocess** for iMessages

```
Mac mini (192.168.1.200)
└── mac-bridge MCP server (port 4000)
    ├── messages_list_unread
    ├── messages_get_conversation
    ├── reminders_list
    └── reminders_get_list

k8s ExternalName Service:
mac-bridge.agents.svc.cluster.local → 192.168.1.200:4000
```

---

## Security Considerations

- No secrets committed to git (all via `kubectl create secret`)
- Agent has read-only RBAC for k8s resources
- Google OAuth uses minimum required scopes
- MCP servers have no LoadBalancer — ClusterIP only (not exposed outside cluster)
- Mac bridge is LAN-only (not externally routable)
- `activeDeadlineSeconds: 1800` prevents runaway jobs
- `MAX_TURNS` env var caps the agentic loop
