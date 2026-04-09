# Agent Configuration Reference (`agent.yaml`)

Each agent is defined by `prompts/<name>/agent.yaml`. The generator script
(`scripts/deploy_agent.py`) reads this file and produces all Kubernetes
resources — no per-agent k8s directory is needed.

## Minimal example

```yaml
name: my-agent
cron:
  schedule: "0 9 * * 1-5"
```

`name` is always required. `cron.schedule` is required when `type: cron` (the
default). Everything else uses the defaults listed below.

## Full schema with defaults

```yaml
# ── Identity ────────────────────────────────────────────────────────────────
name: my-agent               # REQUIRED. Used for all k8s resource names.

# ── Agent type ──────────────────────────────────────────────────────────────
type: cron                   # cron | service

# ── Model ───────────────────────────────────────────────────────────────────
model: claude-opus-4-6       # Any Claude model ID.

# ── Runner tuning ───────────────────────────────────────────────────────────
runner:
  maxTokens: 8192            # Max tokens per Claude response.
  maxTurns: 50               # Hard cap on agentic loop turns before giving up.
  turnDelay: 15              # Seconds to wait between turns (rate-limit buffer).
  toolResultMaxChars: 3000   # Truncate tool results longer than this.

# ── Service config ──────────────────────────────────────────────────────────
# Required when type: service. The cron block is ignored for service agents.
service:
  port: 8080                 # HTTP server port. Default: 8080
  resultTtlSeconds: 3600     # Seconds to retain completed run results in memory. Default: 3600

# ── Cron schedule ───────────────────────────────────────────────────────────
# Required when type: cron.
cron:
  schedule: "0 5 * * *"           # REQUIRED for type: cron. Standard cron expression.
  timezone: UTC                    # IANA timezone string (e.g. America/Denver).
  concurrencyPolicy: Forbid        # Forbid | Allow | Replace
  activeDeadlineSeconds: 1800      # Hard pod timeout (30 min). Prevents runaway loops.
  backoffLimit: 1                  # Retry attempts on failure.
  successfulJobsHistoryLimit: 3    # Completed job pods to keep.
  failedJobsHistoryLimit: 3        # Failed job pods to keep.

# ── Kubernetes resource limits ───────────────────────────────────────────────
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

# ── Persistent memory ────────────────────────────────────────────────────────
# When enabled, a PersistentVolume and PVC are created and mounted at /memory.
# The agent runner's built-in memory_* tools read and write from this path.
memory:
  enabled: false             # Set true to provision NFS-backed storage.
  size: 500Mi
  nfsServer: ""              # Required when enabled: true. e.g. soma.bhavana.local
  nfsPath: ""                # Required when enabled: true. e.g. /kube-volumes/my-agent

# ── MCP servers ─────────────────────────────────────────────────────────────
# Inline definition. The generator converts this block to mcp.json and stores
# it in the ConfigMap alongside AGENT.md. transport defaults to sse when omitted.
# tools is an optional allowlist — omit to expose all tools from that server.
mcpServers: {}
# Example:
#   home-assistant:
#     url: "http://192.168.1.55:8086/mcp"
#     transport: streamable_http   # streamable_http | sse
#     tools:                       # optional allowlist
#       - ha_get_overview
#       - ha_get_entity
#   gmail:
#     url: "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
#   google-calendar:
#     url: "http://gcal-mcp.agents.svc.cluster.local:3001/sse"

# ── Secrets injected as env vars ─────────────────────────────────────────────
# ANTHROPIC_API_KEY is always injected automatically from the anthropic-api-key
# secret. List any additional secrets here.
secrets: []
# Example:
#   - envVar: BRIEFING_EMAIL      # env var name inside the container
#     secretName: briefing-config # kubectl secret name
#     secretKey: email            # key within that secret
```

## Field reference

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | — | Agent name; used for all k8s resource names (`<name>-config`, `<name>-manual`, `agent-<name>`) |
| `type` | No | `cron` | `cron` runs as a Kubernetes CronJob. `service` runs as a long-lived Deployment with an HTTP trigger interface. |
| `service.port` | No | `8080` | Port the HTTP server listens on (service agents only) |
| `service.resultTtlSeconds` | No | `3600` | Seconds to retain completed run results in memory before eviction (service agents only) |
| `model` | No | `claude-opus-4-6` | Claude model ID passed to the Anthropic API |
| `runner.maxTokens` | No | `8192` | `max_tokens` per API call |
| `runner.maxTurns` | No | `50` | Max loop iterations; agent exits with a warning if reached |
| `runner.turnDelay` | No | `15` | Seconds between turns; prevents RPM rate limit hits |
| `runner.toolResultMaxChars` | No | `3000` | Tool results longer than this are truncated before being sent back to Claude |
| `cron.schedule` | Yes (cron) | — | Standard 5-field cron expression |
| `cron.timezone` | No | `UTC` | IANA timezone for schedule evaluation and `TZ` env var inside the pod |
| `cron.concurrencyPolicy` | No | `Forbid` | What to do if a job is still running when the next one fires |
| `cron.activeDeadlineSeconds` | No | `1800` | Hard pod kill timeout; prevents infinite loops from consuming resources |
| `cron.backoffLimit` | No | `1` | Pod retry count on failure |
| `cron.successfulJobsHistoryLimit` | No | `3` | Completed pods to retain for inspection |
| `cron.failedJobsHistoryLimit` | No | `3` | Failed pods to retain for inspection |
| `resources.requests.cpu` | No | `100m` | CPU request |
| `resources.requests.memory` | No | `256Mi` | Memory request |
| `resources.limits.cpu` | No | `500m` | CPU limit |
| `resources.limits.memory` | No | `512Mi` | Memory limit |
| `memory.enabled` | No | `false` | Whether to provision a PV+PVC and mount it at `/memory` |
| `memory.size` | No | `500Mi` | PV/PVC storage size |
| `memory.nfsServer` | When memory.enabled | — | NFS server hostname or IP |
| `memory.nfsPath` | When memory.enabled | — | Absolute path on NFS server |
| `mcpServers` | No | `{}` | Map of MCP server name → `{url, transport?, tools?}` |
| `secrets` | No | `[]` | List of `{envVar, secretName, secretKey}` entries for additional secrets |

## Generated Kubernetes resources

For an agent named `my-agent` the generator produces:

| Resource | Name | Notes |
|----------|------|-------|
| ConfigMap | `my-agent-config` | Contains `AGENT.md` and `mcp.json` (generated from `mcpServers`) |
| CronJob | `my-agent` | Only when `type: cron` |
| Job | `my-agent-manual` | Manual trigger; same pod spec as CronJob. Only when `type: cron` |
| Deployment | `my-agent` | Only when `type: service`. 1 replica, always running |
| Service | `my-agent` | ClusterIP. Only when `type: service`. Accessible at `my-agent.agents.svc.cluster.local` |
| PersistentVolume | `agent-my-agent` | Only when `memory.enabled: true` |
| PersistentVolumeClaim | `agent-my-agent` | Only when `memory.enabled: true` |

## Service agent HTTP API

When `type: service`, the runner exposes three endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness/readiness probe. Always returns `200 {"status": "ok"}` |
| `POST` | `/trigger` | Start a run. Returns `202 {"run_id": "...", "status": "running"}` or `409` if already running |
| `GET` | `/status/{run_id}` | Poll result. Returns `{"status": "running\|complete\|failed", ...}` |

Only one run may be active at a time. A second `POST /trigger` while a run is in progress returns `409 Conflict` with the current run's status.

## Deployment commands

```bash
# Preview generated manifests (no apply)
make preview-agent AGENT=my-agent

# Deploy all resources
make deploy-agent AGENT=my-agent

# Update prompt/MCP config on a running agent
make update-agent-config AGENT=my-agent

# Trigger a manual run (cron agents)
make run-agent AGENT=my-agent

# Follow logs (cron agents)
make logs-agent AGENT=my-agent

# Trigger a run (service agents)
make trigger-agent AGENT=my-agent

# Poll for result (service agents)
make status-agent AGENT=my-agent RUN=<run-id>

# Follow logs (service agents)
make logs-service AGENT=my-agent

# Restart deployment after new image/config (service agents)
make restart-service AGENT=my-agent
```
