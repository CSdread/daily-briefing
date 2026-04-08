# agents

Kubernetes-native Claude agent platform for running autonomous AI agents as CronJobs.

## Repository Overview

This repo contains all resources needed to run Claude agents in the `agents` Kubernetes namespace. Each agent is defined by two files in `prompts/<name>/`: an `AGENT.md` prompt and an `agent.yaml` config. The generator script derives all Kubernetes manifests from those files — no per-agent k8s directory needed.

## Directory Structure

```
agents/
├── docs/                       # Documentation and implementation plan
├── k8s/agents/                 # Kubernetes manifests (agents namespace)
│   ├── namespace.yaml          # Namespace definition
│   ├── rbac/                   # ServiceAccount, Role, RoleBinding
│   ├── gmail-mcp/              # Gmail MCP server deployment
│   ├── gcal-mcp/               # Google Calendar MCP server deployment
│   └── mac-bridge/             # ExternalName service for Mac mini bridge
├── runner/                     # Agent runner container (run_agent.py, mcp_client.py, memory.py)
├── mcps/                       # MCP server implementations
│   ├── gmail/                  # Gmail MCP server (Python/FastAPI)
│   └── gcal/                   # Google Calendar MCP server (Python/FastAPI)
├── scripts/
│   └── deploy_agent.py         # Generates and applies k8s manifests from agent.yaml
├── prompts/                    # One directory per agent
│   └── daily-briefing/
│       ├── AGENT.md            # Claude system prompt
│       └── agent.yaml          # All agent config (model, schedule, MCPs, secrets, resources)
└── pyproject.toml              # Python dependencies for scripts (managed via uv)
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
make deploy-ns                        # Namespace
make deploy-rbac                      # ServiceAccount, Role, RoleBinding
make deploy-mcps                      # Gmail, GCal, Mac Bridge deployments
make deploy-agent AGENT=<name>        # Generate + apply all agent resources
make preview-agent AGENT=<name>       # Print generated manifests without applying
make update-agent-config AGENT=<name> # Reload ConfigMap (prompt + mcp.json) only
```

### Check Status

```bash
make status             # CronJobs, Jobs, and Pods summary
```

### Manually Trigger a Run

```bash
make run-agent AGENT=<name>    # Delete + apply manual Job
make logs-agent AGENT=<name>   # Follow logs from manual Job
```

## Agent Memory

The daily briefing agent uses a persistent filesystem-based memory store mounted at `/memory` in the agent container. Memory is backed by an NFS PersistentVolume (`agent-daily-briefing-1` on `soma.bhavana.local`).

Memory is organized into seven areas:

| Path | Purpose |
|------|---------|
| `/memory/people/{slug}.json` | Known people — names, aliases, relationships inferred from communications |
| `/memory/email_threads/{thread_id}.json` | Cached summaries and importance decisions for Gmail threads |
| `/memory/calendar_events/{event_id}.json` | Metadata only — which dates an event appeared (never substitutes live data) |
| `/memory/escalations.json` | Unresolved flagged items tracked across runs |
| `/memory/projects/{slug}.json` | Ongoing topics that aggregate context from all sources (email, iMessage, reminders, calendar) |
| `/memory/patterns/{slug}.json` | Recurring observations stored for future reference — never used to influence the current briefing |
| `/memory/briefings/{date}.html` | Rolling 7-day archive of sent briefing emails |

The agent reads memory before processing each source and writes updates after the email is sent. Memory is optional — if the volume is not mounted, the agent runs without it. Live sources (calendar, email, sensors) are always authoritative over memory.

See `prompts/daily-briefing/AGENT.md` for the full memory schema and per-source instructions.

## Adding a New Agent

1. Create `prompts/<name>/AGENT.md` with the Claude system prompt
2. Create `prompts/<name>/agent.yaml` — at minimum:
   ```yaml
   name: my-agent
   cron:
     schedule: "0 9 * * 1-5"
   ```
   Add `mcpServers`, `secrets`, `memory`, and tuning fields as needed. See `docs/agent-config.md` for the full schema.
3. Deploy:
   ```bash
   make deploy-agent AGENT=my-agent
   ```

That's it. The generator produces the ConfigMap, CronJob, manual Job, and storage resources automatically. See `docs/secrets.md` for creating any secrets referenced in `agent.yaml`.

## Local Development

Python scripts in this repo (e.g. `scripts/deploy_agent.py`) use [uv](https://docs.astral.sh/uv/) for environment and dependency management.

```bash
# One-time setup — create venv and install dependencies
uv venv
uv sync

# Run a script
uv run scripts/deploy_agent.py <agent-name>

# Install a new dependency
uv add <package>
```

The virtual environment is created at `.venv/` (gitignored). `uv run` activates it automatically — no need to source it manually.

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

## Tool Architecture

The agent runner exposes two categories of tools to Claude:

**Built-in tools** (in-process, no network call):
| Tool | Purpose |
|------|---------|
| `memory_read` | Read a file from `/memory` |
| `memory_write` | Write/overwrite a file in `/memory` |
| `memory_list` | List a `/memory` directory |
| `memory_delete` | Delete a file from `/memory` |

Implemented in `runner/memory.py`. Built-in tools are checked first in the dispatch loop.

**External MCP tools** (HTTP/SSE):
The agent runner connects to each configured MCP server, discovers available tools, and proxies tool calls during the Claude agentic loop. Implemented in `runner/mcp_client.py`. See `docs/mcp-setup.md` for MCP server setup.

The runner module structure:
```
runner/
  run_agent.py    # main agentic loop and prompt loading
  mcp_client.py   # MCP session management, tool discovery, HTTP dispatch
  memory.py       # built-in memory_* tools, in-process filesystem access
  Dockerfile
  requirements.txt
```

## Timezone

The cluster timezone for CronJobs is configured as `America/Denver` (Mountain Time). The `timeZone` field in CronJob specs requires Kubernetes 1.27+.

## Related Resources

- `bhavana/k8s/mcp/` — Existing MCP servers (ha-mcp at 192.168.1.55:8086, github-mcp at 192.168.1.56:8082)
- `docs/plan.md` — Full phased implementation plan
- `docs/architecture.md` — System architecture
