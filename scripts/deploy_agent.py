#!/usr/bin/env python3
"""
Agent deployment generator.

Reads prompts/<name>/agent.yaml and prompts/<name>/AGENT.md, generates all
required Kubernetes manifests (ConfigMap, CronJob, manual Job, PV+PVC), and
optionally applies them.

Usage:
  python3 scripts/deploy_agent.py <agent-name> [flags]

Flags:
  --apply          Generate manifests and apply via kubectl apply -f -
  --dry-run        Generate manifests and print to stdout (default)
  --config-only    Regenerate and apply only the ConfigMap (prompt + mcp.json)
  --run            Delete existing manual Job and apply a new one
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent
NAMESPACE = "agents"
RUNNER_IMAGE = "csdread/agent-runner"

# Defaults for all optional fields
DEFAULTS = {
    "type": "cron",
    "model": "claude-opus-4-6",
    "runner": {
        "maxTokens": 8192,
        "maxTurns": 50,
        "turnDelay": 15,
        "toolResultMaxChars": 3000,
    },
    "cron": {
        "timezone": "UTC",
        "concurrencyPolicy": "Forbid",
        "activeDeadlineSeconds": 1800,
        "backoffLimit": 1,
        "successfulJobsHistoryLimit": 3,
        "failedJobsHistoryLimit": 3,
    },
    "service": {
        "port": 8080,
        "resultTtlSeconds": 3600,
    },
    "resources": {
        "requests": {"cpu": "100m", "memory": "256Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    },
    "memory": {
        "enabled": False,
        "size": "500Mi",
        "nfsServer": "",
        "nfsPath": "",
    },
    "mcpServers": {},
    "secrets": [],
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into a copy of base."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(agent_name: str) -> tuple[dict, str]:
    """Load and merge agent.yaml with defaults. Returns (config, prompt_text)."""
    agent_dir = REPO_ROOT / "prompts" / agent_name
    yaml_path = agent_dir / "agent.yaml"
    md_path = agent_dir / "AGENT.md"

    if not yaml_path.exists():
        print(f"ERROR: {yaml_path} not found", file=sys.stderr)
        sys.exit(1)
    if not md_path.exists():
        print(f"ERROR: {md_path} not found", file=sys.stderr)
        sys.exit(1)

    raw = yaml.safe_load(yaml_path.read_text())
    if not raw.get("name"):
        print("ERROR: agent.yaml must specify 'name'", file=sys.stderr)
        sys.exit(1)

    config = deep_merge(DEFAULTS, raw)

    if config["type"] not in ("cron", "service"):
        print(f"ERROR: unknown type '{config['type']}' — must be 'cron' or 'service'", file=sys.stderr)
        sys.exit(1)

    if config["type"] == "cron" and not config.get("cron", {}).get("schedule"):
        print("ERROR: agent.yaml must specify cron.schedule for type: cron", file=sys.stderr)
        sys.exit(1)

    if config["type"] == "service" and config.get("cron", {}).get("schedule"):
        print("WARNING: cron.schedule is set but type is 'service' — the schedule will be ignored",
              file=sys.stderr)

    return config, md_path.read_text()


def mcp_servers_to_json(mcp_servers: dict) -> str:
    """Convert agent.yaml mcpServers block to mcp.json format."""
    out: dict = {}
    for name, cfg in mcp_servers.items():
        entry: dict = {"url": cfg["url"]}
        if "transport" in cfg:
            entry["transport"] = cfg["transport"]
        if "tools" in cfg:
            entry["tools"] = cfg["tools"]
        out[name] = entry
    return json.dumps({"mcpServers": out}, indent=2)


def runner_image_tag() -> str:
    """Read RUNNER_TAG from Makefile, fall back to 'latest'."""
    makefile = REPO_ROOT / "Makefile"
    if makefile.exists():
        for line in makefile.read_text().splitlines():
            if line.startswith("RUNNER_TAG"):
                parts = line.split("?=", 1) if "?=" in line else line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip()
    return "latest"


def build_configmap(config: dict, prompt: str) -> dict:
    name = config["name"]
    mcp_json = mcp_servers_to_json(config["mcpServers"])
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{name}-config",
            "namespace": NAMESPACE,
        },
        "data": {
            "AGENT.md": prompt,
            "mcp.json": mcp_json,
        },
    }


def _timezone(config: dict) -> str:
    """Return the configured timezone, checking cron block for backwards compat."""
    return config.get("cron", {}).get("timezone", "UTC")


def build_pod_spec(config: dict, mode: str = "cron") -> dict:
    """Build a pod spec for cron (restartPolicy=Never) or service (with probes) mode."""
    name = config["name"]
    runner = config["runner"]
    resources = config["resources"]
    tag = runner_image_tag()

    env = [
        {
            "name": "ANTHROPIC_API_KEY",
            "valueFrom": {"secretKeyRef": {"name": "anthropic-api-key", "key": "key"}},
        },
        {"name": "AGENT_MD_PATH", "value": "/config/AGENT.md"},
        {"name": "MCP_CONFIG_PATH", "value": "/config/mcp.json"},
        {"name": "CLAUDE_MODEL", "value": config["model"]},
        {"name": "MAX_TOKENS", "value": str(runner["maxTokens"])},
        {"name": "MAX_TURNS", "value": str(runner["maxTurns"])},
        {"name": "TURN_DELAY", "value": str(runner["turnDelay"])},
        {"name": "TOOL_RESULT_MAX_CHARS", "value": str(runner["toolResultMaxChars"])},
        {"name": "TZ", "value": _timezone(config)},
    ]

    if mode == "service":
        svc = config["service"]
        env += [
            {"name": "SERVICE_MODE", "value": "true"},
            {"name": "SERVICE_PORT", "value": str(svc["port"])},
            {"name": "RESULT_TTL_SECONDS", "value": str(svc["resultTtlSeconds"])},
        ]

    for secret in config["secrets"]:
        env.append({
            "name": secret["envVar"],
            "valueFrom": {
                "secretKeyRef": {
                    "name": secret["secretName"],
                    "key": secret["secretKey"],
                }
            },
        })

    volume_mounts = [
        {"name": "agent-config", "mountPath": "/config", "readOnly": True},
    ]
    volumes = [
        {"name": "agent-config", "configMap": {"name": f"{name}-config"}},
    ]

    if config["memory"]["enabled"]:
        volume_mounts.append({"name": "agent-memory", "mountPath": "/memory"})
        volumes.append({
            "name": "agent-memory",
            "persistentVolumeClaim": {"claimName": f"agent-{name}"},
        })

    container: dict = {
        "name": "agent-runner",
        "image": f"{RUNNER_IMAGE}:{tag}",
        "imagePullPolicy": "Always",
        "env": env,
        "volumeMounts": volume_mounts,
        "resources": resources,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
    }

    if mode == "service":
        port = config["service"]["port"]
        container["ports"] = [{"containerPort": port, "protocol": "TCP"}]
        container["livenessProbe"] = {
            "httpGet": {"path": "/health", "port": port},
            "periodSeconds": 30,
            "failureThreshold": 3,
        }
        container["readinessProbe"] = {
            "httpGet": {"path": "/health", "port": port},
            "initialDelaySeconds": 5,
            "periodSeconds": 10,
        }

    pod_spec: dict = {
        "serviceAccountName": "agent-runner",
        "containers": [container],
        "volumes": volumes,
    }

    if mode == "cron":
        pod_spec["restartPolicy"] = "Never"

    return pod_spec


def build_cronjob(config: dict) -> dict:
    name = config["name"]
    cron = config["cron"]
    pod_spec = build_pod_spec(config, mode="cron")

    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": name, "type": "agent"},
        },
        "spec": {
            "schedule": cron["schedule"],
            "timeZone": cron["timezone"],
            "concurrencyPolicy": cron["concurrencyPolicy"],
            "successfulJobsHistoryLimit": cron["successfulJobsHistoryLimit"],
            "failedJobsHistoryLimit": cron["failedJobsHistoryLimit"],
            "jobTemplate": {
                "spec": {
                    "activeDeadlineSeconds": cron["activeDeadlineSeconds"],
                    "backoffLimit": cron["backoffLimit"],
                    "template": {
                        "metadata": {
                            "labels": {"app": name, "type": "agent-job"},
                        },
                        "spec": pod_spec,
                    },
                }
            },
        },
    }


def build_manual_job(config: dict) -> dict:
    name = config["name"]
    cron = config["cron"]
    pod_spec = build_pod_spec(config, mode="cron")

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": f"{name}-manual",
            "namespace": NAMESPACE,
            "labels": {"app": name, "type": "agent-job"},
        },
        "spec": {
            "activeDeadlineSeconds": cron["activeDeadlineSeconds"],
            "backoffLimit": cron["backoffLimit"],
            "template": {
                "metadata": {
                    "labels": {"app": name, "type": "agent-job"},
                },
                "spec": pod_spec,
            },
        },
    }


def build_deployment(config: dict) -> dict:
    name = config["name"]
    pod_spec = build_pod_spec(config, mode="service")

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": name, "type": "agent"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name, "type": "agent-service"}},
                "spec": pod_spec,
            },
        },
    }


def build_k8s_service(config: dict) -> dict:
    name = config["name"]
    port = config["service"]["port"]

    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": name},
        },
        "spec": {
            "selector": {"app": name},
            "ports": [{"port": port, "targetPort": port, "protocol": "TCP"}],
            "type": "ClusterIP",
        },
    }


def build_storage(config: dict) -> list[dict]:
    name = config["name"]
    mem = config["memory"]
    pv_name = f"agent-{name}"
    pvc_name = f"agent-{name}"

    pv = {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {
            "name": pv_name,
            "labels": {"app": name},
        },
        "spec": {
            "capacity": {"storage": mem["size"]},
            "accessModes": ["ReadWriteMany"],
            "persistentVolumeReclaimPolicy": "Retain",
            "nfs": {
                "server": mem["nfsServer"],
                "path": mem["nfsPath"],
            },
        },
    }

    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": NAMESPACE,
        },
        "spec": {
            "accessModes": ["ReadWriteMany"],
            "resources": {"requests": {"storage": mem["size"]}},
            "volumeName": pv_name,
            "storageClassName": "",
        },
    }

    return [pv, pvc]


def render_manifests(config: dict, prompt: str, include_storage: bool = True) -> str:
    docs = [build_configmap(config, prompt)]

    if config["type"] == "cron":
        docs.append(build_cronjob(config))
        docs.append(build_manual_job(config))
    elif config["type"] == "service":
        docs.append(build_deployment(config))
        docs.append(build_k8s_service(config))

    if include_storage and config["memory"]["enabled"]:
        mem = config["memory"]
        if not mem["nfsServer"] or not mem["nfsPath"]:
            print("ERROR: memory.nfsServer and memory.nfsPath are required when memory.enabled: true",
                  file=sys.stderr)
            sys.exit(1)
        docs.extend(build_storage(config))

    return "---\n" + "\n---\n".join(
        yaml.dump(doc, default_flow_style=False, sort_keys=False) for doc in docs
    )


def kubectl_apply(manifests: str) -> None:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifests,
        text=True,
        capture_output=False,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


def kubectl_apply_configmap_only(config: dict, prompt: str) -> None:
    doc = build_configmap(config, prompt)
    manifests = yaml.dump(doc, default_flow_style=False, sort_keys=False)
    kubectl_apply(manifests)


def kubectl_run(config: dict, prompt: str) -> None:
    name = config["name"]
    job_name = f"{name}-manual"
    subprocess.run(
        ["kubectl", "delete", "job", job_name, "-n", NAMESPACE, "--ignore-not-found"],
        check=True,
    )
    doc = build_manual_job(config)
    manifests = yaml.dump(doc, default_flow_style=False, sort_keys=False)
    kubectl_apply(manifests)
    print(f"Job started. Run: kubectl logs -n {NAMESPACE} -f job/{job_name} -c agent-runner")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and optionally apply Kubernetes manifests for an agent."
    )
    parser.add_argument("agent", help="Agent name (directory under prompts/)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply all generated manifests")
    mode.add_argument("--dry-run", action="store_true", help="Print manifests to stdout (default)")
    mode.add_argument("--config-only", action="store_true", help="Apply ConfigMap only")
    mode.add_argument("--run", action="store_true", help="Delete + apply manual Job")
    args = parser.parse_args()

    config, prompt = load_config(args.agent)

    if args.config_only:
        kubectl_apply_configmap_only(config, prompt)
    elif args.run:
        kubectl_run(config, prompt)
    elif args.apply:
        manifests = render_manifests(config, prompt)
        kubectl_apply(manifests)
    else:
        # Default: dry-run / print
        manifests = render_manifests(config, prompt)
        print(manifests)


if __name__ == "__main__":
    main()
