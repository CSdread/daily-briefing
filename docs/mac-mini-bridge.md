# Mac Mini Bridge Setup Guide

The Mac mini bridge provides access to Apple-exclusive data sources: iMessages and Reminders (iCloud). It runs a Python MCP server that the Kubernetes agent runner connects to over the local network.

---

## Prerequisites

- Mac mini on the local network
- iCloud signed in with the same Apple ID used for Messages and Reminders
- Messages.app open and authorized
- Reminders.app with iCloud sync enabled
- Python 3.12+ installed (via Homebrew)
- Static DHCP lease or static IP configured (e.g., `192.168.1.200`)

---

## Mac Mini Setup

### 1. Install Dependencies

```bash
# Install Homebrew if not present
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python
brew install python@3.12

# Create a directory for the bridge
mkdir -p ~/agents-bridge
```

### 2. Install Python Packages

```bash
cd ~/agents-bridge
pip3 install mcp pyobjc-framework-EventKit starlette uvicorn
```

### 3. Deploy the Bridge Server

The bridge server code lives in `mcps/mac-bridge/` in this repository. Copy it to the Mac mini (or clone the repo):

```bash
# Option A: Copy from another machine
scp -r mcps/mac-bridge/ username@mac-mini.local:~/agents-bridge/

# Option B: Clone the repo on the Mac mini
git clone <repo-url> ~/agents-bridge/
```

### 4. Test the Bridge Server

```bash
cd ~/agents-bridge/
python3 server.py
# Should print: "Mac Bridge MCP server running on port 4000"
```

Test in another terminal:
```bash
curl -N http://localhost:4000/sse
```

### 5. Install as a launchd Service

Create the plist file at `~/Library/LaunchAgents/com.agents.mac-bridge.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agents.mac-bridge</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>/Users/USERNAME/agents-bridge/server.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/USERNAME/agents-bridge/bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/USERNAME/agents-bridge/bridge-error.log</string>
    <key>WorkingDirectory</key>
    <string>/Users/USERNAME/agents-bridge</string>
</dict>
</plist>
```

Replace `USERNAME` with the actual macOS username.

Load the service:
```bash
launchctl load ~/Library/LaunchAgents/com.agents.mac-bridge.plist
launchctl start com.agents.mac-bridge
```

Verify it's running:
```bash
launchctl list | grep agents
curl http://localhost:4000/health
```

### 6. macOS Permissions

The first time the server accesses Messages and Reminders, macOS will prompt for permission. You can pre-authorize:

- **System Preferences → Privacy & Security → Automation** — Allow Terminal/Python to control Messages
- **System Preferences → Privacy & Security → Reminders** — Allow Terminal/Python

For headless operation, ensure the Mac mini does not require login (System Preferences → Users & Groups → Login Options → Automatic Login).

---

## Kubernetes Configuration

### ExternalName Service

The file `k8s/agents/mac-bridge/service.yaml` creates a Kubernetes service that resolves to the Mac mini:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mac-bridge
  namespace: agents
spec:
  type: ExternalName
  externalName: 192.168.1.200  # Update to actual Mac mini IP
  ports:
    - port: 4000
      targetPort: 4000
```

Update the IP address, then apply:
```bash
kubectl apply -f k8s/agents/mac-bridge/service.yaml
```

Test from inside the cluster:
```bash
kubectl run -n agents debug --rm -it --image=curlimages/curl -- \
  curl http://mac-bridge.agents.svc.cluster.local:4000/health
```

---

## Available Tools

The Mac bridge exposes these MCP tools:

| Tool | Description |
|------|-------------|
| `messages_list_conversations` | List all iMessage conversations with unread counts |
| `messages_get_unread` | Get unread messages across all conversations |
| `messages_get_conversation` | Get messages in a specific conversation (by handle) |
| `reminders_list_all` | List all reminders with completion status and due dates |
| `reminders_get_incomplete` | Get only incomplete reminders |
| `reminders_get_by_list` | Get reminders in a specific list (e.g., "Home", "Work") |
| `reminders_get_due_today` | Get reminders due today or overdue |

---

## Troubleshooting

### AppleScript permissions

If Messages tools return empty results or errors:
```bash
# Grant accessibility permissions to Python
sudo sqlite3 /Library/Application\ Support/com.apple.TCC/TCC.db \
  "INSERT OR REPLACE INTO access VALUES('kTCCServiceAppleEvents','com.apple.python3',...)"
```

Or open System Preferences → Security & Privacy → Accessibility and add Terminal.

### Service not starting on boot

```bash
launchctl list | grep agents
# If not listed:
launchctl unload ~/Library/LaunchAgents/com.agents.mac-bridge.plist
launchctl load ~/Library/LaunchAgents/com.agents.mac-bridge.plist
```

### Checking logs

```bash
tail -f ~/agents-bridge/bridge.log
tail -f ~/agents-bridge/bridge-error.log
```

### Mac mini IP changed

Update `k8s/agents/mac-bridge/service.yaml` with the new IP and reapply:
```bash
kubectl apply -f k8s/agents/mac-bridge/service.yaml
```
