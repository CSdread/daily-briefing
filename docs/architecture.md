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
│  │  │  PVC: agent-daily-briefing  →  NFS: soma.bhavana.local        │   │ │
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
     ▼ (Pass 2: write memory updates via built-in tools)
     ├─ memory_write calendar_events/* (append shown_on dates)
     ├─ memory_write / memory_delete email_threads/* (update summaries, prune old)
     ├─ memory_write people/* (new/updated relationship inferences)
     └─ memory_write escalations.json (increment counters, mark resolved)
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
| `/memory/people/{slug}.json` | Name, aliases, email, relationship, stable notes | Enrich output with known relationships; avoid re-inferring each run |
| `/memory/email_threads/{thread_id}.json` | Summary, importance, timestamps, shown count | Skip re-reading unchanged threads; surface persistent action items |
| `/memory/calendar_events/{event_id}.json` | Event ID, dates shown (metadata only) | Track which dates an event appeared; never substitutes live calendar data |
| `/memory/escalations.json` | Unresolved flagged items with counters | Track items not actioned across multiple days |
| `/memory/projects/{slug}.json` | Name, description, open items, source refs, summary | Aggregate context from all sources under an ongoing topic |
| `/memory/patterns/{slug}.json` | Recurring observation, dates seen, sources | Store recognized patterns for future use — never read back during a run |
| `/memory/briefings/{date}.html` | Full HTML of generated email | Rolling 7-day archive of sent briefings |

### Built-in Memory Tools

Memory is accessed via four tools registered natively in the runner (not via any MCP server):

| Tool | Description |
|------|-------------|
| `memory_read` | Read a file at a path relative to `/memory` |
| `memory_write` | Write/overwrite a file; creates parent directories |
| `memory_list` | List contents of a `/memory` subdirectory |
| `memory_delete` | Delete a file |

These are implemented in `runner/memory.py` and dispatched in-process by `runner/run_agent.py` before the MCP tool lookup. They are registered alongside MCP tools in the Anthropic API call so Claude can call them the same way as any other tool.

### Two-Pass Pattern

1. **Before fetching:** load known projects list, then per-source call `memory_read` / `memory_list` to skip redundant API calls, enrich names, and match items to projects.
2. **After sending:** batch all `memory_write` / `memory_delete` calls in order: calendar events → email threads → people → escalations → projects → patterns → briefing archive → index.

Memory is optional. If `memory_read index.md` returns an error (volume not mounted), the agent runs without it.

**Key constraint:** memory never overrides live data. Calendar event details, email content, and sensor values always come from their live sources. If memory and a live source conflict, trust the live source and update memory. People notes must contain only stable biographical facts — never calendar events or time-sensitive information.

---

## RBAC Design

The `agent-runner` ServiceAccount has minimal, read-only permissions:

| Resource | Verbs | Scope |
|----------|-------|-------|
| configmaps | get, list, watch | agents namespace |
| pods | get, list, watch | agents namespace |
| jobs | get, list, watch | agents namespace |

No write access to any Kubernetes resources. MCP tools and built-in memory tools are the only way the agent interacts with external systems or persists state.

---

## Agent Configuration

Each agent is defined entirely by two files in `prompts/<name>/`:

```
prompts/daily-briefing/
├── AGENT.md      → the Claude system prompt
└── agent.yaml    → all configuration (model, schedule, resources, MCP servers, secrets)
```

`agent.yaml` is the single source of truth. The generator script (`scripts/deploy_agent.py`)
reads it and produces all required Kubernetes resources — ConfigMap, CronJob, manual Job,
PV, and PVC — so no per-agent k8s directory is needed.

### Config → Kubernetes mapping

```
agent.yaml
├── model / runner.*        → env vars on the agent-runner container
├── cron.schedule / .timezone → CronJob spec
├── resources.*             → container resource requests and limits
├── secrets[]               → secretKeyRef env vars
├── memory.*                → PV + PVC (only when memory.enabled: true)
└── mcpServers              → converted to mcp.json, stored in ConfigMap

ConfigMap: <name>-config
├── AGENT.md    → mounted at /config/AGENT.md
└── mcp.json    → mounted at /config/mcp.json (generated from mcpServers block)
```

To update a running agent's prompt or MCP config:
```bash
make update-agent-config AGENT=daily-briefing
# Regenerates and applies the ConfigMap only. Next job run picks it up.
```

---

## Tool Architecture

The runner presents two categories of tools to Claude:

### 1. Built-in Tools (in-process)

Implemented in `runner/memory.py`, dispatched directly by the runner without any network call. Currently: `memory_read`, `memory_write`, `memory_list`, `memory_delete`.

```
Claude tool_use (memory_*)
     │
     ▼
run_agent.py dispatch
     │
     ▼  (BUILTIN_TOOL_NAMES check)
memory.py → call_builtin_tool()
     │
     ▼
/memory filesystem (NFS PVC)
```

### 2. External MCP Tools (HTTP)

Implemented in `runner/mcp_client.py`. All MCP servers use HTTP/SSE or StreamableHTTP transport for compatibility with Kubernetes networking.

```
MCP Server (Python FastAPI)
├── GET /sse      → SSE stream for MCP protocol
├── POST /message → Client-to-server messages
└── GET /health   → Liveness probe
```

```
Claude tool_use (any other tool)
     │
     ▼
run_agent.py dispatch
     │
     ▼  (tool_server_map lookup)
mcp_client.py → call_mcp_tool()
     │
     ▼
MCP server (HTTP/SSE)
```

Tool dispatch order: built-in tools are checked first; if not matched, the MCP routing table is used. Both tool sets are registered together in the Anthropic API call so Claude sees them as a unified tool list.

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
