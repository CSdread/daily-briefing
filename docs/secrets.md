# Secret Management

All secrets are created with `kubectl create secret` and are never committed to this repository. This document lists all required secrets and how to create them.

---

## Namespace

All secrets live in the `agents` namespace:

```bash
kubectl apply -f k8s/agents/namespace.yaml
```

---

## Required Secrets

### 1. Anthropic API Key

```bash
kubectl create secret generic anthropic-api-key \
  --from-literal=key=sk-ant-api03-... \
  -n agents
```

Referenced in CronJob as:
```yaml
env:
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: anthropic-api-key
        key: key
```

---

### 2. Gmail OAuth Credentials

Required after completing the OAuth2 authorization flow (see `docs/mcp-setup.md`).

```bash
kubectl create secret generic gmail-oauth \
  --from-file=credentials.json=./credentials.json \
  --from-file=token.json=./token.json \
  -n agents
```

Referenced in Gmail MCP deployment as mounted volume at `/oauth/`.

> **Note:** The `token.json` contains a refresh token. Google refresh tokens expire after 6 months of inactivity. If the Gmail MCP stops working, re-run the OAuth flow to generate a new token.

---

### 3. Google Calendar OAuth Credentials

The Calendar API can share the same Google Cloud project and credentials as Gmail if both scopes were authorized together.

```bash
kubectl create secret generic gcal-oauth \
  --from-file=credentials.json=./credentials.json \
  --from-file=token.json=./gcal-token.json \
  -n agents
```

Referenced in GCal MCP deployment as mounted volume at `/oauth/`.

---

### 4. Briefing Email Destination

```bash
kubectl create secret generic briefing-config \
  --from-literal=email=daniel@example.com \
  -n agents
```

Referenced in CronJob as env var `BRIEFING_EMAIL`.

---

### 5. iCloud Credentials (Mac Bridge)

Required for the Reminders tools in the Mac Bridge MCP server.
Generate an **app-specific password** at [appleid.apple.com](https://appleid.apple.com) — do not use your regular Apple ID password.

```bash
kubectl create secret generic icloud-credentials \
  --from-literal=apple_id=your-apple-id@example.com \
  --from-literal=app_password=xxxx-xxxx-xxxx-xxxx \
  -n agents
```

Referenced in the mac-bridge DaemonSet as env vars `ICLOUD_APPLE_ID` and `ICLOUD_APP_PASSWORD`.

For the native (launchd) mode, set these directly in `com.agents.mac-bridge.plist`.

---

### 6. Home Assistant MCP Token (optional override)

The existing HA MCP in the `mcp` namespace uses its own token. If a separate read-only HA token is desired for the agents:

```bash
kubectl create secret generic ha-agent-token \
  --from-literal=token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9... \
  -n agents
```

---

## Verifying Secrets

```bash
# List all secrets in agents namespace
kubectl get secrets -n agents

# Describe a secret (shows keys but not values)
kubectl describe secret anthropic-api-key -n agents

# Decode a secret value (for debugging)
kubectl get secret anthropic-api-key -n agents -o jsonpath='{.data.key}' | base64 -d
```

---

## Secret Rotation

### Anthropic API Key

```bash
kubectl create secret generic anthropic-api-key \
  --from-literal=key=sk-ant-api03-NEW-KEY \
  -n agents \
  --dry-run=client -o yaml | kubectl apply -f -
```

No pod restart needed — the next CronJob run picks up the new value.

### Google OAuth Tokens

If a token expires:
1. Delete the old token file locally
2. Re-run the authorization script: `python mcps/gmail/authorize.py`
3. Update the secret:

```bash
kubectl create secret generic gmail-oauth \
  --from-file=credentials.json=./credentials.json \
  --from-file=token.json=./token.json \
  -n agents \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart MCP deployment to pick up new token
kubectl rollout restart deployment/gmail-mcp -n agents
```
