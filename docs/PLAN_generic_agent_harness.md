# Plan: Generic Agent Harness

## Status: Implemented on branch `generic-agent-harness`

## Goal

Replace per-agent `k8s/agents/<name>/` directories with a single source of truth:
`prompts/<name>/agent.yaml` + `prompts/<name>/AGENT.md`.
A generator script derives all k8s manifests — including the `mcp.json` ConfigMap entry —
from those two files alone.

---

## Open Questions (confirm before starting)

1. **Script vs. Helm**: The plan proposes a Python generator script (stdlib + PyYAML). Is that preferred, or should this use Helm instead?
2. **k8s/agents/daily-briefing deletion**: Should the existing per-agent directory be deleted as part of migration, or kept alongside the new approach initially?
3. **`service` type**: Long-running API service is noted as future work — should `agent.yaml` include a stub/placeholder for it, or just a docs note?

---

## Phase 1 — Define `agent.yaml` schema

`prompts/<name>/agent.yaml` is the canonical config file. Only `name` and `cron.schedule`
are required — every other field has a sensible default.

### Full schema (all fields with defaults)

```yaml
# ── Required ────────────────────────────────────────────────────────────────
name: my-agent                    # used for all k8s resource names

# ── Agent type ──────────────────────────────────────────────────────────────
type: cron                        # cron | service (service = future long-running Deployment)

# ── Model ───────────────────────────────────────────────────────────────────
model: claude-opus-4-6            # any Claude model ID

# ── Runner tuning ───────────────────────────────────────────────────────────
runner:
  maxTokens: 8192
  maxTurns: 50
  turnDelay: 15                   # seconds between turns (rate-limit buffer)
  toolResultMaxChars: 3000

# ── Cron schedule (required when type: cron) ────────────────────────────────
cron:
  schedule: "0 5 * * *"          # REQUIRED for type: cron
  timezone: UTC                   # IANA timezone string
  concurrencyPolicy: Forbid
  activeDeadlineSeconds: 1800     # hard 30-min timeout
  backoffLimit: 1
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3

# ── Kubernetes resource limits ───────────────────────────────────────────────
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

# ── Persistent memory (NFS-backed volume at /memory) ────────────────────────
memory:
  enabled: false                  # set true to mount a PVC at /memory
  size: 500Mi
  nfsServer: ""                   # required when enabled: true
  nfsPath: ""                     # required when enabled: true

# ── MCP servers ─────────────────────────────────────────────────────────────
# Inline definition — generator converts this to mcp.json in the ConfigMap.
# transport defaults to sse when omitted. tools list is an optional whitelist.
mcpServers: {}
# Example:
#   home-assistant:
#     url: "http://192.168.1.55:8086/mcp"
#     transport: streamable_http
#     tools:
#       - ha_get_overview
#       - ha_get_bulk_status
#   gmail:
#     url: "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
#   google-calendar:
#     url: "http://gcal-mcp.agents.svc.cluster.local:3001/sse"

# ── Secrets injected as env vars ─────────────────────────────────────────────
# ANTHROPIC_API_KEY is always injected automatically from secret anthropic-api-key.
# List additional secrets here.
secrets: []
# Example:
#   - envVar: BRIEFING_EMAIL
#     secretName: briefing-config
#     secretKey: email
```

### Minimal example (two required fields + a secret)

```yaml
name: my-agent
cron:
  schedule: "0 9 * * 1-5"
```

That's enough to deploy a working agent. Add `mcpServers`, `secrets`, and `memory` only
when needed.

### daily-briefing example (full)

```yaml
name: daily-briefing
model: claude-sonnet-4-6

cron:
  schedule: "0 5 * * *"
  timezone: America/Denver

runner:
  maxTurns: 50
  turnDelay: 15
  toolResultMaxChars: 3000

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

memory:
  enabled: true
  size: 500Mi
  nfsServer: soma.bhavana.local
  nfsPath: /kube-volumes/agent-daily-briefing-1

mcpServers:
  home-assistant:
    url: "http://192.168.1.55:8086/mcp"
    transport: streamable_http
    tools:
      - ha_get_overview
      - ha_get_bulk_status
      - ha_get_entity
      - ha_search_entities
      - ha_get_history
  gmail:
    url: "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
  google-calendar:
    url: "http://gcal-mcp.agents.svc.cluster.local:3001/sse"
  mac-bridge:
    url: "http://mac-bridge.agents.svc.cluster.local:4000/sse"

secrets:
  - envVar: BRIEFING_EMAIL
    secretName: briefing-config
    secretKey: email
```

---

## Phase 2 — Generator converts `mcpServers` → `mcp.json`

There is no longer a standalone `mcp.json` file. The generator reads `agent.yaml`'s
`mcpServers` block and produces the JSON expected by `run_agent.py`, injecting it as a
key in the ConfigMap alongside `AGENT.md`.

**Input** (`agent.yaml` fragment):
```yaml
mcpServers:
  gmail:
    url: "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
  home-assistant:
    url: "http://192.168.1.55:8086/mcp"
    transport: streamable_http
    tools:
      - ha_get_overview
```

**Output** (injected into ConfigMap as `mcp.json`):
```json
{
  "mcpServers": {
    "gmail": {
      "url": "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
    },
    "home-assistant": {
      "url": "http://192.168.1.55:8086/mcp",
      "transport": "streamable_http",
      "tools": ["ha_get_overview"]
    }
  }
}
```

---

## Phase 3 — Create `scripts/deploy_agent.py`

A Python script (stdlib + PyYAML) that reads `prompts/<name>/agent.yaml` and
`prompts/<name>/AGENT.md` and generates k8s YAML dynamically. No Helm, no Jinja2.

**Generates:**
- `ConfigMap` — `AGENT.md` content + `mcp.json` (converted from `mcpServers` block)
- `CronJob` — schedule, resources, secrets, env, volumes derived from `agent.yaml`
- `Job` (manual trigger) — same pod spec, one-off name
- `PersistentVolume` + `PVC` — only if `memory.enabled: true`

**Flags:**
- `--apply` — pipe to `kubectl apply -f -`
- `--dry-run` — print generated YAML to stdout for inspection
- `--config-only` — regenerate and apply only the ConfigMap (for prompt changes)
- `--run` — delete + apply a manual Job (equivalent to current `make run`)

**Always injected** (no need to list in `secrets`):
```
ANTHROPIC_API_KEY from secret anthropic-api-key / key
```

---

## Phase 4 — Fix hardcoded timezone in runner

`runner/run_agent.py:68` hardcodes `ZoneInfo("America/Denver")` for template variable
substitution. Change it to read from the `TZ` env var (already set by the pod spec via
`cron.timezone` in `agent.yaml`) so the timezone is driven by agent config, not baked
into the image. Falls back to `UTC` if `TZ` is unset.

---

## Phase 5 — Update Makefile

Replace agent-specific targets with generic parameterized targets:

```makefile
deploy-agent AGENT=<name>         # generate + apply all resources
preview-agent AGENT=<name>        # generate + print YAML, no apply
update-agent-config AGENT=<name>  # regenerate + apply ConfigMap only
run-agent AGENT=<name>            # delete + apply manual Job
logs-agent AGENT=<name>           # follow logs from manual Job
```

Existing MCP-server targets (`deploy-mcps`, `restart-gmail`, etc.) remain unchanged —
those are shared infrastructure, not agent-specific.

---

## Phase 6 — Migrate daily-briefing

1. Write `prompts/daily-briefing/agent.yaml` (see full example in Phase 1)
2. Delete `k8s/agents/daily-briefing/` — configmap.yaml, cronjob.yaml, job-manual.yaml,
   storage.yaml, secret-template.yaml, and mcp.json are all now generated or inlined
3. Verify `make deploy-agent AGENT=daily-briefing` reproduces equivalent resources

---

## Phase 7 — Update documentation

- **README.md**: Replace "Adding a New Agent" (current 6-step process) with 2 steps:
  create `prompts/<name>/`, run `make deploy-agent AGENT=<name>`. Update directory
  structure diagram and Makefile targets table.
- **docs/architecture.md**: Add `agent.yaml` to the configuration layer description.
- **New: `docs/agent-config.md`**: Full schema reference for `agent.yaml` with field
  descriptions, defaults, and the minimal example.

---

## What stays the same

- `k8s/agents/namespace.yaml`, `rbac/`, `gmail-mcp/`, `gcal-mcp/`, `mac-bridge/` — static infrastructure manifests, untouched
- Runner image, MCP server images — no changes
- Agent memory structure, tool architecture — no changes

---

## New prompts directory layout (after migration)

```
prompts/
  daily-briefing/
    AGENT.md       # prompt (existing, unchanged)
    agent.yaml     # NEW: all config including mcpServers inline
```

No `mcp.json` file — it is generated into the ConfigMap at deploy time.

---

## Context for resuming

**Codebase location:** `/Users/danielfeinberg/Workspace/agents`

**Key files to read before starting:**
- `prompts/daily-briefing/AGENT.md` — the agent prompt
- `k8s/agents/daily-briefing/cronjob.yaml` — current config (values to migrate)
- `k8s/agents/daily-briefing/mcp.json` — MCP config to inline into agent.yaml
- `runner/run_agent.py` — env vars it reads; hardcoded timezone at line 68
- `Makefile` — targets to replace/augment
- `README.md` — docs to update
- `docs/architecture.md` — docs to update
