# MCP Server Setup Guide

This document covers setting up each MCP server in the platform.

---

## Existing MCP Servers (bhavana cluster)

These MCP servers are already deployed in the `mcp` namespace and are reused:

| Server | URL (in-cluster) | External IP |
|--------|-----------------|-------------|
| Home Assistant | `http://ha-mcp.mcp.svc.cluster.local:8086/sse` | 192.168.1.55:8086 |
| GitHub | `http://github-mcp.mcp.svc.cluster.local:8082` | 192.168.1.56:8082 |

The daily briefing agent can access the Home Assistant MCP directly. The HA MCP image (`ghcr.io/homeassistant-ai/ha-mcp`) exposes an SSE endpoint.

> **Note:** The HA MCP token is currently hardcoded in the mcp namespace deployment. Consider creating a dedicated read-only long-lived access token in Home Assistant and referencing it via a Secret.

---

## Gmail MCP Server

### Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or select existing)
3. Enable the **Gmail API**:
   - APIs & Services → Library → Search "Gmail API" → Enable
4. Create OAuth 2.0 credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: **Desktop App**
   - Download the JSON — save as `credentials.json`

### OAuth Scopes Required

```
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.labels
```

### Authorization Flow (one-time)

```bash
cd mcps/gmail/
pip install -r requirements.txt
python authorize.py
# Opens browser for Google sign-in
# Saves token.json on completion
```

### Creating the Kubernetes Secret

```bash
kubectl create secret generic gmail-oauth \
  --from-file=credentials.json=./credentials.json \
  --from-file=token.json=./token.json \
  -n agents
```

### Deployment

```bash
cd mcps/gmail/
docker build -t csdread/gmail-mcp:1 .
docker push csdread/gmail-mcp:1
kubectl apply -f k8s/agents/gmail-mcp/
kubectl rollout status deployment/gmail-mcp -n agents
```

### Verify

```bash
# Port-forward for local testing
kubectl port-forward -n agents svc/gmail-mcp 3000:3000

# Test SSE connection
curl -N http://localhost:3000/sse
```

---

## Google Calendar MCP Server

The Calendar MCP shares the same Google Cloud project as Gmail.

### Additional API Enablement

1. Enable the **Google Calendar API**:
   - APIs & Services → Library → Search "Google Calendar API" → Enable
2. Add Calendar scope to your OAuth credentials

### OAuth Scopes Required

```
https://www.googleapis.com/auth/calendar.readonly
```

### Combined Authorization (recommended)

Authorize both Gmail and Calendar scopes in a single flow by modifying `mcps/gcal/authorize.py` to include both sets of scopes. This produces a single `token.json` that works for both services.

### Authorization Flow

```bash
cd mcps/gcal/
pip install -r requirements.txt
python authorize.py
```

### Creating the Kubernetes Secret

```bash
kubectl create secret generic gcal-oauth \
  --from-file=credentials.json=./credentials.json \
  --from-file=token.json=./gcal-token.json \
  -n agents
```

### Deployment

```bash
cd mcps/gcal/
docker build -t csdread/gcal-mcp:1 .
docker push csdread/gcal-mcp:1
kubectl apply -f k8s/agents/gcal-mcp/
kubectl rollout status deployment/gcal-mcp -n agents
```

---

## Mac Mini Bridge (Phase 5)

See `docs/mac-mini-bridge.md` for the full setup guide.

Quick summary:
- Runs Python MCP server on Mac mini at port 4000
- Provides iMessage and Reminders tools
- Kubernetes ExternalName service points to Mac mini IP
- Requires Mac mini to be on local network with static IP

---

## MCP Configuration (mcp.json)

The `mcp.json` stored in the `daily-briefing-config` ConfigMap tells the agent runner which servers to connect to:

```json
{
  "mcpServers": {
    "home-assistant": {
      "url": "http://ha-mcp.mcp.svc.cluster.local:8086/sse"
    },
    "gmail": {
      "url": "http://gmail-mcp.agents.svc.cluster.local:3000/sse"
    },
    "google-calendar": {
      "url": "http://gcal-mcp.agents.svc.cluster.local:3001/sse"
    },
    "mac-bridge": {
      "url": "http://mac-bridge.agents.svc.cluster.local:4000/sse"
    }
  }
}
```

### Updating the MCP Config

```bash
kubectl create configmap daily-briefing-config \
  --from-file=AGENT.md=prompts/daily-briefing/AGENT.md \
  --from-file=mcp.json=k8s/agents/daily-briefing/mcp.json \
  -n agents \
  --dry-run=client -o yaml | kubectl apply -f -
```

---

## Troubleshooting

### MCP server not reachable

```bash
# Check pod is running
kubectl get pods -n agents -l app=gmail-mcp

# Check logs
kubectl logs -n agents deployment/gmail-mcp

# Test in-cluster connectivity from a debug pod
kubectl run -n agents debug --rm -it --image=curlimages/curl -- \
  curl -N http://gmail-mcp.agents.svc.cluster.local:3000/sse
```

### OAuth token expired

```bash
# Re-run the authorization flow on your local machine
cd mcps/gmail/
python authorize.py

# Update the secret
kubectl create secret generic gmail-oauth \
  --from-file=credentials.json=./credentials.json \
  --from-file=token.json=./token.json \
  -n agents \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deployment/gmail-mcp -n agents
```

### Agent not calling MCP tools

Check the agent runner logs:
```bash
kubectl logs -n agents job/daily-briefing-<id>
```

Look for:
- `"Connecting to MCP server..."` — should appear for each server
- `"Registered tool: ..."` — tools being discovered
- `"Calling tool: ..."` — tools being called
- `"Failed to connect to MCP server..."` — connectivity issue
