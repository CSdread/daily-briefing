# Plan: Service Agent Type

## Overview

This plan covers adding `type: service` to the agent platform. A service agent runs as a persistent Kubernetes Deployment (rather than a CronJob) with an HTTP server that accepts trigger requests. When triggered, it runs the Claude agentic loop once and returns the result. The server stays alive between triggers, ready for the next request.

**Out of scope for this phase:** input payloads to the trigger call, authentication on the HTTP endpoint, multiple concurrent runs per service.

---

## The Timeout Problem

The naive design — `POST /trigger` blocks until the agent finishes, then returns the result — breaks for agents that run longer than ~10 minutes. Kubernetes ingresses, AWS ALBs, Nginx proxies, and most HTTP clients impose hard timeouts in the 30s–10min range. An agent doing 50 turns with a 15s delay between each takes over 12 minutes to run; the connection will be killed before it completes.

### Proposed Solution: Async Run + Polling

Rather than a single blocking request, the trigger is fire-and-return:

```
POST /trigger
  → 202 Accepted
  → {"run_id": "a1b2c3", "status": "running"}

GET /status/{run_id}
  → {"status": "running", "started_at": "...", "turn": 14}
  OR
  → {"status": "complete", "started_at": "...", "finished_at": "...", "result": "..."}
  OR
  → {"status": "failed", "started_at": "...", "finished_at": "...", "error": "..."}
```

**Why polling over alternatives:**

| Approach | Problem |
|---|---|
| Blocking HTTP | Times out at proxies/clients after ~60s |
| WebSocket | Client complexity, still subject to gateway timeouts |
| SSE / chunked streaming | Better, but many automation clients don't handle SSE well; nginx must be configured with `proxy_read_timeout` |
| Polling | Universally supported, robust against any proxy timeout, simple to implement and call |

**Concurrency:** only one run at a time. A `POST /trigger` while an agent is running returns `409 Conflict` with the current run's status. This prevents rate-limit pile-ups and keeps resource usage predictable. A future phase can add a bounded queue (`service.concurrency: queue`).

**Result retention:** results are kept in-process memory for a configurable TTL (default 1 hour). If memory is enabled for the agent, results are also persisted to `/memory/runs/{run_id}.json` so they survive a pod restart.

---

## HTTP Server Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness/readiness probe. Returns `200 {"status": "ok"}` always. |
| `POST` | `/trigger` | Start a new agent run. Returns `202` with `run_id`, or `409` if already running. |
| `GET` | `/status/{run_id}` | Poll for result. Returns current status and result when complete. |

---

## agent.yaml Schema Changes

New `type: service` with optional `service` block:

```yaml
type: service

service:
  port: 8080                 # HTTP server port. Default: 8080.
  resultTtlSeconds: 3600     # How long to retain a completed run's result in memory. Default: 3600.
```

The existing `cron` block is ignored for service agents. `runner.*`, `resources`, `memory`, `mcpServers`, and `secrets` all behave identically to cron agents.

### Updated agent-config.md additions

| Field | Required | Default | Description |
|---|---|---|---|
| `service.port` | No | `8080` | Port the HTTP server listens on |
| `service.resultTtlSeconds` | No | `3600` | Seconds to retain run results in memory before eviction |

---

## Generated Kubernetes Resources

For a service agent named `my-service`, the generator produces:

| Resource | Kind | Name | Notes |
|---|---|---|---|
| ConfigMap | ConfigMap | `my-service-config` | Same as cron — AGENT.md + mcp.json |
| Deployment | Deployment | `my-service` | 1 replica, always running |
| Service | Service | `my-service` | ClusterIP, exposes `service.port` |
| PersistentVolume | PV | `agent-my-service` | Only when `memory.enabled: true` |
| PersistentVolumeClaim | PVC | `agent-my-service` | Only when `memory.enabled: true` |

No CronJob or manual Job is generated for service agents.

### Deployment spec highlights

- Same container image and env vars as the cron runner
- Adds `SERVICE_MODE=true` env var so the runner boots the HTTP server instead of running the agent directly
- Adds `SERVICE_PORT` env var from `service.port`
- `restartPolicy: Always` (Deployment default)
- Liveness probe: `GET /health` every 30s
- Readiness probe: `GET /health` every 10s, 5s initial delay
- `replicas: 1` — single instance; no horizontal scaling in this phase

### Service spec

ClusterIP service mapping `service.port` → `service.port`. Accessible at `<name>.agents.svc.cluster.local:<port>` from within the cluster.

---

## Runner Changes

### New file: `runner/service_runner.py`

A lightweight HTTP server built on top of the existing `run_agent.py` logic. Uses `asyncio` + Python's built-in `http.server` module (no new dependencies) or a minimal addition of `aiohttp`/`fastapi`.

**Recommended:** add `fastapi` + `uvicorn[standard]` to `runner/requirements.txt`. The MCP servers already use FastAPI; it's proven in this stack and gives clean async route handlers that compose naturally with the existing `asyncio`-based agent loop.

```
runner/
  run_agent.py          # unchanged — the core agentic loop function
  service_runner.py     # NEW — FastAPI app, wraps run_agent() on demand
  mcp_client.py         # unchanged
  memory.py             # unchanged
  Dockerfile            # updated entrypoint (see below)
```

### `service_runner.py` structure

```python
# Pseudocode outline
app = FastAPI()
state = RunState()   # holds current_run: RunRecord | None, results: dict[run_id, RunRecord]

@app.get("/health")
async def health(): return {"status": "ok"}

@app.post("/trigger")
async def trigger():
    if state.current_run and state.current_run.status == "running":
        raise HTTPException(409, detail={"run_id": state.current_run.run_id, "status": "running"})
    run_id = uuid4().hex
    state.current_run = RunRecord(run_id=run_id, status="running")
    asyncio.create_task(execute_run(run_id))
    return {"run_id": run_id, "status": "running"}

@app.get("/status/{run_id}")
async def status(run_id: str):
    record = state.results.get(run_id) or (
        state.current_run if state.current_run and state.current_run.run_id == run_id else None
    )
    if not record:
        raise HTTPException(404)
    return record.to_dict()

async def execute_run(run_id: str):
    try:
        result = await run_agent()   # existing function, returns final text
        state.results[run_id] = RunRecord(status="complete", result=result, ...)
    except Exception as e:
        state.results[run_id] = RunRecord(status="failed", error=str(e), ...)
    finally:
        state.current_run = None
    evict_expired_results(state)
```

### `run_agent.py` changes

The existing `run_agent()` function currently logs the final output but returns `None`. It needs a small change to **return the final text output** from the last `end_turn` response so `service_runner.py` can include it in the result. No other behavioral changes.

### `Dockerfile` entrypoint change

Currently: `CMD ["python", "run_agent.py"]`

New: the entrypoint checks `SERVICE_MODE`:

```dockerfile
CMD ["sh", "-c", "if [ \"$SERVICE_MODE\" = 'true' ]; then uvicorn service_runner:app --host 0.0.0.0 --port $SERVICE_PORT; else python run_agent.py; fi"]
```

Or, cleaner, a small `entrypoint.sh` wrapper script:

```bash
#!/bin/sh
if [ "$SERVICE_MODE" = "true" ]; then
    exec uvicorn service_runner:app --host 0.0.0.0 --port "${SERVICE_PORT:-8080}"
else
    exec python run_agent.py
fi
```

This keeps the same image for both cron and service agents — no second Dockerfile needed.

---

## `deploy_agent.py` Changes

### New builder functions

```python
def build_deployment(config: dict) -> dict: ...
def build_service(config: dict) -> dict: ...
```

These follow the same pattern as `build_cronjob` / `build_manual_job`.

### `build_pod_spec` changes

- Accept a `mode` parameter (`"cron"` or `"service"`)
- When `mode == "service"`: add `SERVICE_MODE=true`, `SERVICE_PORT=<port>` env vars; set `restartPolicy: Always`; add liveness/readiness probes; remove `restartPolicy: Never`
- The TZ env var currently reads from `config["cron"]["timezone"]` — this should be refactored to read from a top-level `timezone` field (or fall back to `config.get("cron", {}).get("timezone", "UTC")`) so service agents can also set it

### `render_manifests` changes

```python
if config["type"] == "cron":
    docs.append(build_cronjob(config))
    docs.append(build_manual_job(config))
elif config["type"] == "service":
    docs.append(build_deployment(config))
    docs.append(build_service(config))
```

### Validation changes

- `cron.schedule` required only when `type == "cron"` — already partially handled, just needs the error message to be type-aware
- `type: service` should warn if `cron.schedule` is set (it will be ignored)

---

## Template System Consideration

The current approach — building Python dicts and calling `yaml.dump` — is compact and has no extra dependencies. The main downside is that the generated YAML structure is spread across many small functions, making it hard to visualize the full manifest.

**Recommendation for this phase:** stay with the dict approach. The service additions (Deployment + Service) are well-understood, bounded resources. A template system (Jinja2 `.yaml.j2` files under `templates/`) would be worth revisiting if a third or fourth agent type is added, or if the manifests get significantly more complex. Add a note in `deploy_agent.py` about the tradeoff.

If we do switch later, the natural split would be:

```
templates/
  configmap.yaml.j2
  cronjob.yaml.j2
  manual-job.yaml.j2
  deployment.yaml.j2
  service.yaml.j2
  pv.yaml.j2
  pvc.yaml.j2
```

Each template owns its own resource. The generator becomes a thin renderer that loads the right templates based on `config["type"]`. Jinja2 is the obvious choice (already a transitive dependency of many Python tools); whitespace control for YAML indentation is the main gotcha.

---

## Makefile Changes

New targets mirroring the existing cron targets:

```makefile
deploy-agent AGENT=<name>      # unchanged — works for both types
preview-agent AGENT=<name>     # unchanged
update-agent-config AGENT=<name>  # unchanged

# Service-specific
trigger-agent AGENT=<name>     # POST /trigger, prints run_id
status-agent AGENT=<name> RUN=<run_id>  # GET /status/<run_id>, prints result
logs-service AGENT=<name>      # kubectl logs -f deployment/<name>
```

`trigger-agent` and `status-agent` use `kubectl exec` or `kubectl port-forward` + `curl` — no external ingress required for local use.

---

## New agent.yaml example

```yaml
name: home-control
type: service

model: claude-opus-4-6

service:
  port: 8080
  resultTtlSeconds: 1800

runner:
  maxTokens: 8192
  maxTurns: 30
  turnDelay: 10

resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

mcpServers:
  home-assistant:
    url: "http://192.168.1.55:8086/mcp"
    transport: streamable_http

secrets:
  - envVar: HA_TOKEN
    secretName: ha-config
    secretKey: token
```

---

## Implementation Phases

### Phase 1 — Runner HTTP server
1. Add `fastapi` + `uvicorn[standard]` to `runner/requirements.txt`
2. Create `runner/service_runner.py` with `/health`, `/trigger`, `/status/{run_id}`
3. Modify `run_agent()` to return final text output
4. Add `entrypoint.sh` (preferred over inline shell in `CMD`) and update `Dockerfile`
5. Build and push new runner image

### Phase 2 — Generator changes
1. Add `build_deployment()` and `build_service()` to `deploy_agent.py`
2. Update `build_pod_spec()` to support service mode (env vars, probes, restart policy)
3. Update `render_manifests()` to branch on `type`
4. Validate `type: service` config (warn on stray `cron` block)
5. Update `docs/agent-config.md` with new fields and generated resources table

### Phase 3 — Makefile + docs
1. Add `trigger-agent`, `status-agent`, `logs-service` Makefile targets
2. Update `README.md` with service agent section
3. Update `docs/architecture.md` with Deployment/Service diagram

### Phase 4 (later) — Inputs
Add optional JSON body to `POST /trigger` → passed to the agent as environment variables or injected into the prompt. Schema TBD.

---

## Open Questions

1. **Should `/trigger` accept a `wait` query param** (e.g. `?wait=60`) that blocks for up to N seconds before falling back to async? This could simplify callers for fast agents without changing the default behavior.

2. **Result persistence on pod restart**: if the pod is killed mid-run (OOM, node eviction), the in-progress result is lost. Should we write a `running` sentinel to `/memory/runs/{run_id}.json` at start so callers can detect an interrupted run? Only relevant when `memory.enabled: true`.

3. **Timezone for service agents**: currently `TZ` is read from `cron.timezone`. Should there be a top-level `timezone` field that both types share, with `cron.timezone` as an alias?

4. **ClusterIP only vs NodePort/ingress**: for now ClusterIP is sufficient (triggered from within the cluster or via port-forward). If external triggering is needed later, a NodePort or Ingress can be layered on. Worth calling out in docs so it's intentional, not forgotten.
