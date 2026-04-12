# Agent Builder Skill

You are an expert at building new Claude agents for this Kubernetes-native agent platform. Use this skill whenever you are asked to design, scaffold, or write a new agent for this system.

---

## System Overview

Agents in this platform run as Kubernetes **CronJobs** (scheduled) or **Deployments** (persistent, HTTP-triggered). Each agent is fully defined by two files:

```
prompts/<agent-name>/
  AGENT.md      ← Claude system prompt (the agent's brain)
  agent.yaml    ← All configuration: schedule, model, MCPs, secrets, memory, skills
```

The deploy script (`scripts/deploy_agent.py`) reads these files and generates all Kubernetes resources — ConfigMap, CronJob or Deployment, manual Job, PV/PVC — with no per-agent k8s directory needed.

The runner container (`runner/run_agent.py`) handles the Claude agentic loop, MCP tool proxying, and skill loading. Skills are injected as separate system prompt blocks before `AGENT.md`, allowing reusable instructions to be shared across agents and cached independently by Anthropic's prompt cache.

---

## Creating a New Agent

### Step 1 — Write `agent.yaml`

Minimum viable config:

```yaml
name: my-agent
cron:
  schedule: "0 9 * * 1-5"   # required for type: cron
```

Full schema with all fields and defaults:

```yaml
# ── Identity ────────────────────────────────────────────────────────────────
name: my-agent               # REQUIRED. Drives all k8s resource names.

# ── Agent type ──────────────────────────────────────────────────────────────
type: cron                   # cron (CronJob) | service (Deployment + HTTP trigger)

# ── Model ───────────────────────────────────────────────────────────────────
model: claude-sonnet-4-6     # Any Claude model ID. Default: claude-opus-4-6

# ── Runner tuning ───────────────────────────────────────────────────────────
runner:
  maxTokens: 8192
  maxTurns: 50
  turnDelay: 15              # Seconds between turns — rate-limit buffer
  toolResultMaxChars: 3000   # Truncate long tool results

# ── Cron schedule (required when type: cron) ─────────────────────────────
cron:
  schedule: "0 9 * * 1-5"
  timezone: America/Denver   # IANA timezone. Also sets TZ env var in pod.
  concurrencyPolicy: Forbid  # Forbid | Allow | Replace
  activeDeadlineSeconds: 1800
  backoffLimit: 1
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3

# ── Service config (required when type: service) ──────────────────────────
service:
  port: 8080
  resultTtlSeconds: 3600

# ── Resource limits ──────────────────────────────────────────────────────
resources:
  requests: { cpu: 100m, memory: 256Mi }
  limits:   { cpu: 500m, memory: 512Mi }

# ── Persistent memory (NFS-backed) ────────────────────────────────────────
memory:
  enabled: false
  size: 500Mi
  nfsServer: soma.bhavana.local
  nfsPath: /kube-volumes/agent-my-agent

# ── Skills ────────────────────────────────────────────────────────────────
# Skills are markdown files in skills/ loaded as separate system prompt blocks
# before AGENT.md. They are cached independently by the Anthropic prompt cache.
skills: []
# Example:
#   - daily-briefing-email   # loads skills/daily-briefing-email.md

# ── MCP servers ───────────────────────────────────────────────────────────
mcpServers: {}
# Example:
#   gmail:
#     url: "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
#   google-calendar:
#     url: "http://gcal-mcp.agents.svc.cluster.local:3001/sse"
#   home-assistant:
#     url: "http://192.168.1.55:8086/mcp"
#     transport: streamable_http
#     tools:                     # optional allowlist
#       - ha_get_entity
#       - ha_get_overview
#   mac-bridge:
#     url: "http://mac-bridge.agents.svc.cluster.local:4000/sse"

# ── Additional secrets ────────────────────────────────────────────────────
# ANTHROPIC_API_KEY is always injected automatically.
secrets: []
# Example:
#   - envVar: BRIEFING_EMAIL
#     secretName: briefing-config
#     secretKey: email
```

### Step 2 — Write `AGENT.md`

The system prompt Claude receives. Use these template variables — the runner substitutes them at startup:

| Variable | Value |
|----------|-------|
| `{{ TODAY }}` | e.g. `Saturday, April 12, 2026` |
| `{{ DATE }}` | e.g. `2026-04-12` |
| `{{ TIME }}` | e.g. `5:02 AM MDT` |
| `{{ TZ_OFFSET }}` | e.g. `-06:00` |
| Any `envVar` from the `secrets` block | e.g. `{{ BRIEFING_EMAIL }}` |

Template variables are also substituted in any loaded skill files, so skills can reference `{{ TODAY }}`, `{{ BRIEFING_EMAIL }}`, etc.

**AGENT.md best practices:**
- Be specific about what the agent should gather, produce, and do
- Define clear success/exit criteria so the agent reaches `end_turn` cleanly
- If the agent sends emails or calls external APIs, make idempotency explicit (e.g., "only call `gmail_send` once")
- Structure instructions as ordered steps the agent can follow top-to-bottom
- Keep generic reusable sections (email templates, output formats) in a skill instead of AGENT.md

### Step 3 — Deploy

```bash
# Preview what will be generated (no apply)
make preview-agent AGENT=my-agent

# Deploy all resources
make deploy-agent AGENT=my-agent

# Update only the prompt/MCP config on a running agent (no image rebuild needed)
make update-agent-config AGENT=my-agent

# Trigger a manual run (cron agents)
make run-agent AGENT=my-agent

# Follow logs from the manual run
make logs-agent AGENT=my-agent
```

---

## Available Infrastructure

### MCP Servers

| Name | Cluster URL | Transport | Available Tools |
|------|-------------|-----------|-----------------|
| `gmail` | `http://gmail-mcp.agents.svc.cluster.local:3000/sse` | SSE | `gmail_search`, `gmail_read_message`, `gmail_list_labels`, `gmail_send` |
| `google-calendar` | `http://gcal-mcp.agents.svc.cluster.local:3001/sse` | SSE | `gcal_list_calendars`, `gcal_list_events`, `gcal_get_event` |
| `home-assistant` | `http://192.168.1.55:8086/mcp` | streamable_http | `ha_get_entity`, `ha_get_history`, `ha_search_entities`, `ha_get_overview`, `ha_get_bulk_status` |
| `mac-bridge` | `http://mac-bridge.agents.svc.cluster.local:4000/sse` | SSE | `messages_list_conversations`, `messages_get_unread`, `messages_get_conversation`, `reminders_list_all`, `reminders_get_incomplete`, `reminders_get_by_list`, `reminders_get_due_today` |

Use the optional `tools` allowlist in `mcpServers` to expose only the tools your agent needs.

### Built-in Memory Tools

Available to all agents automatically — no MCP config needed:

| Tool | Description |
|------|-------------|
| `memory_read` | Read a file at a path relative to `/memory` |
| `memory_write` | Write or overwrite a file; creates parent directories |
| `memory_list` | List contents of a `/memory` subdirectory |
| `memory_delete` | Delete a file |

Memory requires `memory.enabled: true` in `agent.yaml` and an NFS path. If the volume is not mounted, `memory_read index.md` will return an error — design agents to detect this and continue gracefully.

### Secrets

`ANTHROPIC_API_KEY` is always injected automatically from the `anthropic-api-key` Kubernetes secret. Declare additional secrets in `agent.yaml` under `secrets` — each becomes an environment variable in the pod and a template variable available in `AGENT.md` and skill files.

To create a new secret:
```bash
kubectl create secret generic my-secret -n agents --from-literal=my-key=value
```

---

## Skills System

Skills are markdown files in `skills/` that get loaded as **separate system prompt blocks** before `AGENT.md` in every Anthropic API call. Each skill block has its own `cache_control: ephemeral` marker, so skills are cached independently and don't burn tokens on every turn.

Reference skills in `agent.yaml`:
```yaml
skills:
  - daily-briefing-email   # loads skills/daily-briefing-email.md
```

**When to extract a skill:**
- The instructions apply to multiple agents (e.g., an email format template, a response style guide)
- The content is stable and should be cached separately from the agent-specific prompt
- You want to separate *how to present output* from *what data to gather*

**Available skills:**

| Skill | File | Purpose |
|-------|------|---------|
| `daily-briefing-email` | `skills/daily-briefing-email.md` | HTML template, section layout, and style guide for daily briefing emails |
| `agent-builder` | `skills/agent-builder.md` | This file — reference for building new agents |

---

## One-Shot Tool Enforcement

The runner enforces that certain tools may only be called once per run. Currently: `gmail_send`. A second call to any protected tool returns an error to Claude instead of executing. Design agent prompts accordingly — mention explicitly "only call `gmail_send` once."

---

## Generating a New Agent

When asked to create a new agent, produce:

1. **`prompts/<name>/agent.yaml`** — full config with appropriate schedule, model, MCPs, memory, and skills
2. **`prompts/<name>/AGENT.md`** — focused system prompt with clear gather → synthesize → output → done structure

Then show the user the deploy command:
```bash
make deploy-agent AGENT=<name>
```

If the agent sends email, reference the `daily-briefing-email` skill in `agent.yaml` and remove format/style instructions from `AGENT.md`.

If the agent needs a new secret, provide the `kubectl create secret` command before the deploy step.
