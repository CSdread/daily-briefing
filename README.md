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

### Deploy Namespace and RBAC

```bash
kubectl apply -f k8s/agents/namespace.yaml
kubectl apply -f k8s/agents/rbac/
```

### Deploy MCP Servers

```bash
kubectl apply -f k8s/agents/gmail-mcp/
kubectl apply -f k8s/agents/gcal-mcp/
kubectl apply -f k8s/agents/mac-bridge/
```

### Deploy Daily Briefing CronJob

```bash
kubectl apply -f k8s/agents/daily-briefing/
```

### Check Status

```bash
kubectl get pods -n agents
kubectl get cronjobs -n agents
kubectl logs -n agents -l job-name=<job-name> --tail=100
```

### Manually Trigger a Job Run

```bash
kubectl create job -n agents --from=cronjob/daily-briefing daily-briefing-manual-$(date +%s)
```

### Watch Job Logs

```bash
kubectl logs -n agents -f job/daily-briefing-manual-<timestamp>
```

## Adding a New Agent

1. Create a prompt in `prompts/<agent-name>/AGENT.md`
2. Create a ConfigMap in `k8s/agents/<agent-name>/configmap.yaml` with the AGENT.md content
3. Create a CronJob in `k8s/agents/<agent-name>/cronjob.yaml`
4. Create any required secrets (see `docs/secrets.md`)
5. Deploy: `kubectl apply -f k8s/agents/<agent-name>/`

## Building Container Images

### Agent Runner

```bash
cd runner/
docker build -t csdread/agent-runner:latest .
docker push csdread/agent-runner:latest
```

### Gmail MCP Server

```bash
cd mcps/gmail/
docker build -t csdread/gmail-mcp:latest .
docker push csdread/gmail-mcp:latest
```

### Google Calendar MCP Server

```bash
cd mcps/gcal/
docker build -t csdread/gcal-mcp:latest .
docker push csdread/gcal-mcp:latest
```

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
