# agents

Kubernetes-native Claude agent platform for running autonomous AI agents as CronJobs.

## Repository Overview

This repo contains all resources needed to run Claude agents in the `agents` Kubernetes namespace. Agents are defined by an `AGENT.md` prompt file loaded from a ConfigMap. The agent runner container reads this prompt, connects to configured MCP servers, and runs Claude autonomously.

## Directory Structure

```
agents/
├── docs/                       # Documentation and implementation plan
├── k8s/agents/                 # All Kubernetes manifests (agents namespace)
│   ├── namespace.yaml          # Namespace definition
│   ├── rbac/                   # ServiceAccount, Role, RoleBinding
│   ├── daily-briefing/         # Daily briefing CronJob manifests
│   │   ├── cronjob.yaml        # Scheduled CronJob (5am MT daily)
│   │   ├── job-manual.yaml     # One-off manual job trigger
│   │   ├── configmap.yaml      # AGENT.md + mcp.json ConfigMap
│   │   ├── storage.yaml        # PV + PVC for agent memory (NFS)
│   │   └── secret-template.yaml
│   ├── gmail-mcp/              # Gmail MCP server deployment
│   ├── gcal-mcp/               # Google Calendar MCP server deployment
│   └── mac-bridge/             # ExternalName service for Mac mini bridge
├── runner/                     # Agent runner container (reads AGENT.md, calls Claude)
├── mcps/                       # MCP server implementations
│   ├── gmail/                  # Gmail MCP server (Python/FastAPI)
│   └── gcal/                   # Google Calendar MCP server (Python/FastAPI)
└── prompts/                    # Agent prompt files (AGENT.md per agent)
    └── daily-briefing/
```

## Deployment

### Prerequisites

- kubectl configured for the cluster
- `agents` namespace created
- Required secrets created (see `docs/secrets.md`)
- MCP server images built and pushed
- NFS directory created on the storage server: `mkdir /kube-volumes/agent-daily-briefing-1`

Use `make help` to see all available targets.

### Full Deploy (first time)

```bash
make deploy-all
```

This runs: namespace → RBAC → MCP servers → ConfigMap → storage (PV/PVC) → CronJob.

### Deploy Individual Components

```bash
make deploy-ns          # Namespace
make deploy-rbac        # ServiceAccount, Role, RoleBinding
make deploy-mcps        # Gmail, GCal, Mac Bridge deployments
make deploy-storage     # NFS PV + PVC for agent memory
make deploy-briefing    # Storage + CronJob (storage is a prerequisite)
make update-config      # Reload AGENT.md + mcp.json into ConfigMap
```

### Check Status

```bash
make status             # CronJobs, Jobs, and Pods summary
```

### Manually Trigger a Run

```bash
make run                # Delete + reapply job-manual.yaml
make run-once           # Unique timestamped job (keeps history)
make logs               # Follow logs from briefing-manual job
```

## Agent Memory

The daily briefing agent uses a persistent filesystem-based memory store mounted at `/memory` in the agent container. Memory is backed by an NFS PersistentVolume (`agent-daily-briefing-1` on `soma.bhavana.local`).

Memory is organized into four areas:

| Path | Purpose |
|------|---------|
| `/memory/people/{slug}.json` | Known people — names, aliases, relationships inferred from communications |
| `/memory/email_threads/{thread_id}.json` | Cached summaries and importance decisions for Gmail threads |
| `/memory/calendar_events/{event_id}.json` | Calendar event IDs and the dates they were shown |
| `/memory/escalations.json` | Unresolved flagged items tracked across runs |

The agent reads memory before processing each source (to skip redundant work and enrich context with known relationships), and writes updates after the email is sent. Memory is optional — if the volume is not mounted, the agent runs without it.

See `prompts/daily-briefing/AGENT.md` for the full memory schema and per-source instructions.

## Adding a New Agent

1. Create a prompt in `prompts/<agent-name>/AGENT.md`
2. Create a ConfigMap in `k8s/agents/<agent-name>/configmap.yaml` with the AGENT.md content
3. Create a CronJob in `k8s/agents/<agent-name>/cronjob.yaml`
4. If the agent needs persistent memory, create a `storage.yaml` (PV + PVC) following the pattern in `k8s/agents/daily-briefing/storage.yaml`
5. Create any required secrets (see `docs/secrets.md`)
6. Deploy: `kubectl apply -f k8s/agents/<agent-name>/`

## Building Container Images

Use `make` targets — image tags are pinned in the Makefile header.

```bash
make release            # Build + push all images
make release-runner     # Build + push agent-runner only
make release-gmail      # Build + push gmail-mcp only
make release-gcal       # Build + push gcal-mcp only
make release-mac-bridge # Build + push mac-bridge only
```

After pushing a new runner image, update `RUNNER_TAG` in the Makefile and run `make deploy-briefing`.

## Secret Management

Secrets are never committed to this repository. See `docs/secrets.md` for the full list of required secrets and how to create them with `kubectl create secret`.

## MCP Server Architecture

All MCP servers expose an HTTP/SSE endpoint. The agent runner connects to each server, discovers available tools, and proxies tool calls during the Claude agentic loop. See `docs/mcp-setup.md` for setup instructions.

## Timezone

The cluster timezone for CronJobs is configured as `America/Denver` (Mountain Time). The `timeZone` field in CronJob specs requires Kubernetes 1.27+.

## Related Resources

- `bhavana/k8s/mcp/` — Existing MCP servers (ha-mcp at 192.168.1.55:8086, github-mcp at 192.168.1.56:8082)
- `docs/plan.md` — Full phased implementation plan
- `docs/architecture.md` — System architecture
