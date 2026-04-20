# Agent Configuration Reference (`agent.yaml`)

Each agent is defined by `prompts/<name>/agent.yaml`. The generator script
(`scripts/deploy_agent.py`) reads this file and produces all Kubernetes
resources — no per-agent k8s directory is needed.

## Minimal example

```yaml
name: my-agent
trigger:
  kind: cron
  cron:
    schedule: "0 9 * * 1-5"
```

`name` is always required. `trigger.cron.schedule` is required when
`trigger.kind: cron`. Everything else uses the defaults listed below. The legacy
`cron:` form (without the `trigger:` wrapper) still works but emits a
`DeprecationWarning`.

## Full schema with defaults

```yaml
# ── Identity ────────────────────────────────────────────────────────────────
name: my-agent               # REQUIRED. Used for all k8s resource names.

# ── Trigger block (preferred) ────────────────────────────────────────────────
# Replaces the legacy `type` + `cron` top-level fields. See §"Trigger block"
# below for the full schema. The legacy form still works — see §"Legacy shim".
#
# trigger:
#   kind: cron               # cron | https | queue | manual

# ── Agent type (DEPRECATED — use trigger.kind instead) ───────────────────────
type: cron                   # cron | service (service = future long-running Deployment)

# ── Model ───────────────────────────────────────────────────────────────────
model: claude-opus-4-6       # Any Claude model ID.

# ── Runner tuning ───────────────────────────────────────────────────────────
runner:
  maxTokens: 8192            # Max tokens per Claude response.
  maxTurns: 50               # Hard cap on agentic loop turns before giving up.
  turnDelay: 15              # Seconds to wait between turns (rate-limit buffer).
  toolResultMaxChars: 3000   # Truncate tool results longer than this.

# ── Cron schedule ───────────────────────────────────────────────────────────
# Required when type: cron.
cron:
  schedule: "0 5 * * *"           # REQUIRED for type: cron. Standard cron expression.
  timezone: UTC                    # IANA timezone string (e.g. America/Denver).
  concurrencyPolicy: Forbid        # Forbid | Allow | Replace
  activeDeadlineSeconds: 1800      # Hard pod timeout (30 min). Prevents runaway loops.
  backoffLimit: 1                  # Retry attempts on failure.
  successfulJobsHistoryLimit: 50   # Completed job pods to keep.
  failedJobsHistoryLimit: 50       # Failed job pods to keep.

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

# ── Skills ──────────────────────────────────────────────────────────────────
# Markdown files from skills/ to inject as separate system prompt blocks before
# AGENT.md. Each block gets its own cache_control marker so stable skill content
# is cached independently from the agent-specific prompt.
skills: []
# Example:
#   - daily-briefing-email   # loads skills/daily-briefing-email.md

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
| `name` | Yes | — | Agent name; used for all k8s resource names (`<name>-config`, `<name>-manual`, `agent-<name>`). Maximum 42 characters (longest derived name `<name>-idm-<16hex>` must fit in the 63-char Kubernetes limit). |
| `trigger.kind` | No | `manual` | `cron` \| `https` \| `queue` \| `manual`. Determines which Kubernetes resources are generated. |
| `trigger.runtime.timezone` | No | `UTC` | IANA timezone string; sets `TZ` env var inside the pod and the CronJob `timeZone` field. |
| `trigger.runtime.activeDeadlineSeconds` | No | `1800` | Hard pod kill timeout (30 min); prevents infinite loops from consuming resources. |
| `trigger.runtime.backoffLimit` | No | `1` | Pod retry count on failure. |
| `type` | No | `cron` | **Deprecated** — use `trigger.kind` instead. `cron` runs as a Kubernetes CronJob. `service` (future) will run as a long-lived Deployment. |
| `model` | No | `claude-opus-4-6` | Claude model ID passed to the Anthropic API |
| `runner.maxTokens` | No | `8192` | `max_tokens` per API call |
| `runner.maxTurns` | No | `50` | Max loop iterations; agent exits with a warning if reached |
| `runner.turnDelay` | No | `15` | Seconds between turns; prevents RPM rate limit hits |
| `runner.toolResultMaxChars` | No | `3000` | Tool results longer than this are truncated before being sent back to Claude |
| `cron.schedule` | Yes (cron) | — | **Deprecated** — use `trigger.cron.schedule`. Standard 5-field cron expression. |
| `cron.timezone` | No | `UTC` | **Deprecated** — use `trigger.runtime.timezone`. IANA timezone for schedule evaluation and `TZ` env var inside the pod. |
| `cron.concurrencyPolicy` | No | `Forbid` | **Deprecated** — use `trigger.cron.concurrencyPolicy`. What to do if a job is still running when the next one fires. |
| `cron.activeDeadlineSeconds` | No | `1800` | **Deprecated** — use `trigger.runtime.activeDeadlineSeconds`. Hard pod kill timeout. |
| `cron.backoffLimit` | No | `1` | **Deprecated** — use `trigger.runtime.backoffLimit`. Pod retry count on failure. |
| `cron.successfulJobsHistoryLimit` | No | `50` | **Deprecated** — use `trigger.cron.successfulJobsHistoryLimit`. Completed pods to retain for inspection. |
| `cron.failedJobsHistoryLimit` | No | `50` | **Deprecated** — use `trigger.cron.failedJobsHistoryLimit`. Failed pods to retain for inspection. |
| `resources.requests.cpu` | No | `100m` | CPU request |
| `resources.requests.memory` | No | `256Mi` | Memory request |
| `resources.limits.cpu` | No | `500m` | CPU limit |
| `resources.limits.memory` | No | `512Mi` | Memory limit |
| `memory.enabled` | No | `false` | Whether to provision a PV+PVC and mount it at `/memory` |
| `memory.size` | No | `500Mi` | PV/PVC storage size |
| `memory.nfsServer` | When memory.enabled | — | NFS server hostname or IP |
| `memory.nfsPath` | When memory.enabled | — | Absolute path on NFS server |
| `skills` | No | `[]` | List of skill names to load from `skills/`. Each is injected as a separate cached system prompt block before `AGENT.md`. |
| `mcpServers` | No | `{}` | Map of MCP server name → `{url, transport?, tools?}` |
| `secrets` | No | `[]` | List of `{envVar, secretName, secretKey}` entries for additional secrets |

## Trigger block

The `trigger` block is the preferred way to declare how an agent is invoked.
It replaces the legacy top-level `type` and `cron` fields. All four trigger
kinds share a common `runtime` sub-block for pod-level settings; kind-specific
parameters live in a sub-block named after the kind.

### Full trigger schema

```yaml
trigger:
  kind: cron                 # cron | https | queue | manual  (REQUIRED)

  # ── Fields shared by ALL trigger kinds ───────────────────────────────────
  # Timezone and pod-level timeouts/retries.
  runtime:
    timezone: UTC                    # IANA timezone string. Sets TZ env var inside the pod.
    activeDeadlineSeconds: 1800      # Hard pod kill timeout. Prevents runaway loops.
    backoffLimit: 1                  # Pod retry count on failure.

  # ── cron: schedule-triggered agent ───────────────────────────────────────
  # Required fields when trigger.kind: cron.
  cron:
    schedule: "0 5 * * *"           # REQUIRED. Standard 5-field cron expression.
    concurrencyPolicy: Forbid        # Forbid | Allow | Replace
    successfulJobsHistoryLimit: 50   # Completed job pods to keep.
    failedJobsHistoryLimit: 50       # Failed job pods to keep.

  # ── https: HTTP-triggered agent (async-polling) ───────────────────────────
  # Phase D fills this in. Stub section only.
  https:
    path: /my-agent                  # Route relative to the dispatcher Service.
    tokenSecret:
      secretName: my-agent-trigger   # k8s Secret name containing the trigger token.
      secretKey: token               # Key within the Secret.
    timestampSkewSeconds: 60         # Replay-protection window in seconds.
    payload:
      mode: env                      # env | configmap | none
      envVar: TRIGGER_PAYLOAD        # Env var name when mode: env.
      maxBytes: 16384                # Cap for env mode; above this use mode: configmap.
    rateLimit:
      reqPerSecond: 10               # Per-replica token bucket.
      reqPerMinutePerIp: 60          # Per-source-IP bucket.
    idempotencyTtlSeconds: 600       # Advisory TTL for idempotency key tracking.
    # Response is always async: 202 {runId}. Callers poll /api/runs/:id.

  # ── queue: message-queue-triggered agent ─────────────────────────────────
  # Phase E fills this in. Stub section only.
  queue:
    broker: rabbitmq                 # rabbitmq (v1) | sqs | pubsub | cloudamqp
    rabbitmq:
      connection:
        secretName: rabbitmq-creds   # k8s Secret containing the AMQPS URL.
        secretKey: url               # Key within the Secret (must be amqps://).
      tlsCaSecret:
        secretName: rabbitmq-ca      # Secret containing the CA certificate.
        secretKey: ca.crt
      exchange: agents
      queueName: my-agent-events
      routingKey: my-agent.*
      prefetch: 1
      dlqName: my-agent-events.dlq  # Dead-letter queue name.
      maxDeliveries: 3              # Nack after this many delivery attempts.
      durable: false                 # v1: in-memory queues (no disk persistence).
    concurrency: 1                   # Max simultaneous Jobs spawned from queue messages.
    payload:
      mode: env                      # env | configmap
      envVar: TRIGGER_PAYLOAD
      maxBytes: 16384

  # ── manual: UI-only agent ─────────────────────────────────────────────────
  # Generator emits ONLY the ConfigMap. Jobs are created on-demand by the
  # control-plane when an operator clicks "Run now" in the dashboard.
  manual:
    cleanupAfterRunSeconds: 3600     # TTL for completed Jobs created on-demand.
```

### trigger.runtime fields

| Field | Default | Description |
|-------|---------|-------------|
| `trigger.runtime.timezone` | `UTC` | IANA timezone string; sets `TZ` env var inside the pod and the CronJob `timeZone` field |
| `trigger.runtime.activeDeadlineSeconds` | `1800` | Hard pod kill timeout (30 min); prevents infinite loops |
| `trigger.runtime.backoffLimit` | `1` | Pod retry count on failure |

### trigger.cron fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `trigger.cron.schedule` | Yes (cron) | — | Standard 5-field cron expression |
| `trigger.cron.concurrencyPolicy` | No | `Forbid` | `Forbid` \| `Allow` \| `Replace` |
| `trigger.cron.successfulJobsHistoryLimit` | No | `50` | Completed pods to retain |
| `trigger.cron.failedJobsHistoryLimit` | No | `50` | Failed pods to retain |

### trigger.https fields (Phase D — stub)

`trigger.kind: https` is reserved for Phase D. Declaring it in this phase causes the generator to emit only the ConfigMap plus a commented-out placeholder marker. The dispatcher logic that actually creates Jobs from HTTP requests lands in Phase D.

### trigger.queue fields (Phase E — stub)

`trigger.kind: queue` is reserved for Phase E. Declaring it in this phase causes the generator to emit only the ConfigMap plus a commented-out placeholder marker. The consumer logic that processes queue messages lands in Phase E.

### trigger.manual

`trigger.kind: manual` agents have no automatic trigger. The generator emits only the ConfigMap. Jobs for these agents are created on-demand by the control-plane when an operator clicks "Run now" in the dashboard.

### Legacy shim

The legacy `type` + `cron` top-level fields continue to work for one release.
`scripts/deploy_agent.py` rewrites them internally to the canonical `trigger`
block before generating manifests. A `DeprecationWarning` is emitted (not
printed) when the legacy form is detected.

**Rewrite rules:**

| Legacy input | Canonical trigger.kind | Notes |
|---|---|---|
| `type: cron` + top-level `cron:` block | `cron` | `cron.*` copied into `trigger.cron`; `cron.timezone`, `cron.activeDeadlineSeconds`, `cron.backoffLimit` copied into `trigger.runtime` |
| No `type` field, but top-level `cron:` present | `cron` | Same as above |
| No `type` field and no `cron:` block | `manual` | No schedule is possible; agent is manual-only |

The legacy `type: cron` / top-level `cron` fields will be removed in a future release. Migrate by replacing:

```yaml
# Legacy (still works, emits DeprecationWarning)
type: cron
cron:
  schedule: "0 5 * * *"
  timezone: America/Denver
  activeDeadlineSeconds: 1800
  backoffLimit: 1
```

with:

```yaml
# Preferred
trigger:
  kind: cron
  runtime:
    timezone: America/Denver
    activeDeadlineSeconds: 1800
    backoffLimit: 1
  cron:
    schedule: "0 5 * * *"
```

## Generated Kubernetes resources

For an agent named `my-agent` the generator produces:

| Resource | Name | Notes |
|----------|------|-------|
| ConfigMap | `my-agent-config` | Contains `AGENT.md`, `mcp.json`, and any `skill_<name>.md` files. Always emitted. |
| CronJob | `my-agent` | Only when `trigger.kind: cron` (or legacy `type: cron`) |
| Job | `my-agent-manual` | Emitted alongside CronJob for `trigger.kind: cron`; created on-demand for `manual` kind |
| PersistentVolume | `agent-my-agent` | Only when `memory.enabled: true` |
| PersistentVolumeClaim | `agent-my-agent` | Only when `memory.enabled: true` |

All Job resources (CronJob template + manual Job) are stamped with labels
`agent=<name>`, `trigger-kind=<kind>`, and `managed-by=agent-platform` in
addition to the existing `app` and `type` labels. These labels are the
backbone of run-history queries (`kubectl get jobs -l agent=<name>`).

## Deployment commands

```bash
# Preview generated manifests (no apply)
make preview-agent AGENT=my-agent

# Deploy all resources
make deploy-agent AGENT=my-agent

# Update prompt/MCP config on a running agent
make update-agent-config AGENT=my-agent

# Trigger a manual run
make run-agent AGENT=my-agent

# Follow logs
make logs-agent AGENT=my-agent
```
