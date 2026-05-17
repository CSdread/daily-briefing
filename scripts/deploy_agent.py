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
import warnings
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: uv add pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent
NAMESPACE = "agents"
RUNNER_IMAGE = "csdread/agent-runner"

# Defaults for all optional fields
DEFAULTS = {
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
        "successfulJobsHistoryLimit": 50,
        "failedJobsHistoryLimit": 50,
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
    "skills": [],
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


def _validate_agent_name(name: str) -> None:
    """Exit with an error if the agent name exceeds 42 characters.

    The longest derived resource name is '<agent>-idm-<16hex>' = len(agent) + 21.
    Kubernetes limits resource names to 63 characters, so: 63 - 21 = 42.
    """
    if len(name) > 42:
        print(
            f"ERROR: agent name '{name}' exceeds 42 characters "
            "(max derivable Job name: <agent>-idm-<16hex> must be ≤ 63 chars)",
            file=sys.stderr,
        )
        sys.exit(1)


def load_config(agent_name: str) -> tuple[dict, str]:
    """Load and merge agent.yaml with defaults. Returns (config, prompt_text)."""
    _validate_agent_name(agent_name)
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

    # ── Legacy shim ──────────────────────────────────────────────────────────
    # Normalise old-style `type: cron` + root `cron:` block into the canonical
    # `trigger:` block so all downstream code can read from trigger.* uniformly.
    # Keep config["cron"] populated for any stale readers within this phase.
    if "trigger" not in config:
        raw_cron = config.get("cron", {})
        legacy_schedule = raw_cron.get("schedule")
        if config.get("type") == "cron" and (
            not isinstance(legacy_schedule, str) or not legacy_schedule.strip()
        ):
            print("ERROR: agent.yaml must specify cron.schedule for type: cron", file=sys.stderr)
            sys.exit(1)
        has_cron_block = bool(raw_cron.get("schedule"))
        if has_cron_block:
            warnings.warn(
                f"Agent '{config['name']}': top-level 'cron:' block is deprecated. "
                "Use 'trigger: {{kind: cron, runtime: {{...}}, cron: {{...}}}}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            config["trigger"] = {
                "kind": "cron",
                "runtime": {
                    "timezone": raw_cron.get("timezone", "UTC"),
                    "activeDeadlineSeconds": raw_cron.get("activeDeadlineSeconds", 1800),
                    "backoffLimit": raw_cron.get("backoffLimit", 1),
                },
                "cron": {
                    "schedule": raw_cron["schedule"],
                    "concurrencyPolicy": raw_cron.get("concurrencyPolicy", "Forbid"),
                    "successfulJobsHistoryLimit": raw_cron.get("successfulJobsHistoryLimit", 50),
                    "failedJobsHistoryLimit": raw_cron.get("failedJobsHistoryLimit", 50),
                },
            }
        else:
            warnings.warn(
                f"Agent '{config['name']}': no 'trigger' or 'cron' block found. "
                "Defaulting to trigger.kind: manual.",
                DeprecationWarning,
                stacklevel=2,
            )
            config["trigger"] = {"kind": "manual", "runtime": {}, "manual": {}}

    # Ensure trigger.runtime always exists with defaults filled in.
    trigger = config["trigger"]
    trigger.setdefault("runtime", {})
    trigger["runtime"].setdefault("timezone", "UTC")
    trigger["runtime"].setdefault("activeDeadlineSeconds", 1800)
    trigger["runtime"].setdefault("backoffLimit", 1)

    # Validate cron schedule when kind is cron.
    if trigger["kind"] == "cron":
        schedule = trigger.get("cron", {}).get("schedule") or config.get("cron", {}).get("schedule")
        if not isinstance(schedule, str) or not schedule.strip():
            print(
                "ERROR: agent.yaml trigger.cron.schedule must be a non-empty cron expression",
                file=sys.stderr,
            )
            sys.exit(1)
        # Ensure trigger.cron sub-block exists with defaults.
        trigger.setdefault("cron", {})
        raw_cron = config.get("cron", {})
        trigger["cron"].setdefault("schedule", raw_cron.get("schedule", ""))
        trigger["cron"].setdefault("concurrencyPolicy", raw_cron.get("concurrencyPolicy", "Forbid"))
        trigger["cron"].setdefault("successfulJobsHistoryLimit", raw_cron.get("successfulJobsHistoryLimit", 50))
        trigger["cron"].setdefault("failedJobsHistoryLimit", raw_cron.get("failedJobsHistoryLimit", 50))
        # Keep config["cron"] in sync for stale readers within this phase.
        config["cron"]["timezone"] = trigger["runtime"]["timezone"]
        config["cron"]["activeDeadlineSeconds"] = trigger["runtime"]["activeDeadlineSeconds"]
        config["cron"]["backoffLimit"] = trigger["runtime"]["backoffLimit"]
        config["cron"]["schedule"] = trigger["cron"]["schedule"]
        config["cron"]["concurrencyPolicy"] = trigger["cron"]["concurrencyPolicy"]
        config["cron"]["successfulJobsHistoryLimit"] = trigger["cron"]["successfulJobsHistoryLimit"]
        config["cron"]["failedJobsHistoryLimit"] = trigger["cron"]["failedJobsHistoryLimit"]
    # ── End legacy shim ──────────────────────────────────────────────────────

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


def load_skills(config: dict) -> dict[str, str]:
    """Load skill markdown files listed in config['skills'] from the skills/ directory."""
    result: dict[str, str] = {}
    for skill_name in config.get("skills", []):
        path = REPO_ROOT / "skills" / f"{skill_name}.md"
        if not path.exists():
            print(f"WARNING: skill '{skill_name}' not found at {path}", file=sys.stderr)
            continue
        result[skill_name] = path.read_text()
    return result


def build_configmap(
    config: dict,
    prompt: str,
    skills: dict[str, str] | None = None,
    extra_data: dict[str, str] | None = None,
) -> dict:
    """Build the agent ConfigMap manifest.

    Args:
        config:     Merged agent configuration dict.
        prompt:     Contents of AGENT.md.
        skills:     Optional mapping of skill_name → markdown text; each entry is
                    stored as ``skill_<name>.md`` in the ConfigMap.
        extra_data: Optional additional key→value pairs merged into ``data`` after
                    skills are applied.  Intended for Phase D/E to attach per-trigger
                    config (e.g. ``trigger.json``) without modifying this function's
                    signature again.
    """
    name = config["name"]
    mcp_json = mcp_servers_to_json(config["mcpServers"])
    data: dict[str, str] = {"AGENT.md": prompt, "mcp.json": mcp_json}
    # Embed each skill as skill_<name>.md. The runner globs skill_*.md at startup
    # and injects them as separate system prompt blocks before AGENT.md.
    for skill_name, skill_content in (skills or {}).items():
        data[f"skill_{skill_name}.md"] = skill_content
    if extra_data:
        data.update(extra_data)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{name}-config",
            "namespace": NAMESPACE,
        },
        "data": data,
    }


def build_pod_spec(config: dict, trigger_env: list[dict] | None = None) -> dict:
    name = config["name"]
    runner = config["runner"]
    resources = config["resources"]
    tag = runner_image_tag()

    # Read timezone from trigger.runtime (canonical path); fall back to legacy cron block.
    timezone = config.get("trigger", {}).get("runtime", {}).get(
        "timezone", config.get("cron", {}).get("timezone", "UTC")
    )

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
        {"name": "TZ", "value": timezone},
    ]

    if trigger_env:
        env.extend(trigger_env)

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

    container = {
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

    return {
        "serviceAccountName": "agent-runner",
        "restartPolicy": "Never",
        "containers": [container],
        "volumes": volumes,
    }


def build_cronjob(config: dict) -> dict:
    name = config["name"]
    trigger = config["trigger"]
    runtime = trigger["runtime"]
    cron = trigger["cron"]
    pod_spec = build_pod_spec(config)

    platform_labels = {
        "agent": name,
        "trigger-kind": "cron",
        "managed-by": "agent-platform",
    }

    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": name, "type": "agent", **platform_labels},
        },
        "spec": {
            "schedule": cron["schedule"],
            "timeZone": runtime["timezone"],
            "concurrencyPolicy": cron["concurrencyPolicy"],
            "successfulJobsHistoryLimit": cron["successfulJobsHistoryLimit"],
            "failedJobsHistoryLimit": cron["failedJobsHistoryLimit"],
            "jobTemplate": {
                "metadata": {
                    "labels": {"app": name, "type": "agent-job", **platform_labels},
                },
                "spec": {
                    "activeDeadlineSeconds": runtime["activeDeadlineSeconds"],
                    "backoffLimit": runtime["backoffLimit"],
                    "template": {
                        "metadata": {
                            "labels": {"app": name, "type": "agent-job", **platform_labels},
                        },
                        "spec": pod_spec,
                    },
                },
            },
        },
    }


def build_manual_job(config: dict, trigger_kind: str = "cron") -> dict:
    name = config["name"]
    runtime = config["trigger"]["runtime"]
    pod_spec = build_pod_spec(config)

    platform_labels = {
        "agent": name,
        "trigger-kind": trigger_kind,
        "managed-by": "agent-platform",
    }

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": f"{name}-manual",
            "namespace": NAMESPACE,
            "labels": {"app": name, "type": "agent-job", **platform_labels},
        },
        "spec": {
            "activeDeadlineSeconds": runtime["activeDeadlineSeconds"],
            "backoffLimit": runtime["backoffLimit"],
            "template": {
                "metadata": {
                    "labels": {"app": name, "type": "agent-job", **platform_labels},
                },
                "spec": pod_spec,
            },
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


def render_cron(config: dict, prompt: str, skills: dict[str, str] | None = None) -> list[dict]:
    """Render manifests for trigger.kind: cron. Returns ConfigMap + CronJob + manual Job."""
    return [
        build_configmap(config, prompt, skills),
        build_cronjob(config),
        build_manual_job(config, trigger_kind="cron"),
    ]


# Phase D must modify ONLY this stub. Do not touch render_cron, render_manual,
# or render_manifests dispatcher from Phase D tasks.
def render_https(config: dict, prompt: str, skills: dict[str, str] | None = None) -> list[dict]:
    """Render manifests for trigger.kind: https (STUB — Phase D fills this in).

    Returns ConfigMap plus a commented-out placeholder marker. Does not fail.
    """
    configmap = build_configmap(config, prompt, skills)
    placeholder = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{config['name']}-https-stub",
            "namespace": NAMESPACE,
            "annotations": {
                "agent-platform/stub": "true",
                "agent-platform/phase": "D",
            },
        },
        "data": {
            "README": (
                "# STUB — phase D fills this in.\n"
                "Do not modify render_cron, render_manual, or render_manifests dispatcher.\n"
            ),
        },
    }
    return [configmap, placeholder]


# Phase E must modify ONLY this stub. Do not touch render_cron, render_manual,
# or render_manifests dispatcher from Phase E tasks.
def render_queue(config: dict, prompt: str, skills: dict[str, str] | None = None) -> list[dict]:
    """Render manifests for trigger.kind: queue (STUB — Phase E fills this in).

    Returns ConfigMap plus a commented-out placeholder marker. Does not fail.
    """
    configmap = build_configmap(config, prompt, skills)
    placeholder = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{config['name']}-queue-stub",
            "namespace": NAMESPACE,
            "annotations": {
                "agent-platform/stub": "true",
                "agent-platform/phase": "E",
            },
        },
        "data": {
            "README": (
                "# STUB — phase E fills this in.\n"
                "Do not modify render_cron, render_manual, or render_manifests dispatcher.\n"
            ),
        },
    }
    return [configmap, placeholder]


def render_manual(config: dict, prompt: str, skills: dict[str, str] | None = None) -> list[dict]:
    """Render manifests for trigger.kind: manual. Returns only the ConfigMap.

    Jobs for manual-only agents are created on-demand by the control-plane.
    """
    return [build_configmap(config, prompt, skills)]


def render_manifests(config: dict, prompt: str, skills: dict[str, str] | None = None, include_storage: bool = True) -> str:
    """Dispatch to the appropriate render_* function based on trigger.kind."""
    kind = config["trigger"]["kind"]

    _dispatch = {
        "cron": render_cron,
        "https": render_https,
        "queue": render_queue,
        "manual": render_manual,
    }

    render_fn = _dispatch.get(kind)
    if render_fn is None:
        print(f"ERROR: unknown trigger.kind '{kind}'", file=sys.stderr)
        sys.exit(1)

    docs = render_fn(config, prompt, skills)

    if include_storage and config["memory"]["enabled"]:
        mem = config["memory"]
        if not mem["nfsServer"] or not mem["nfsPath"]:
            print("ERROR: memory.nfsServer and memory.nfsPath are required when memory.enabled: true",
                  file=sys.stderr)
            sys.exit(1)
        docs = list(docs) + build_storage(config)

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


def kubectl_apply_configmap_only(config: dict, prompt: str, skills: dict[str, str] | None = None) -> None:
    doc = build_configmap(config, prompt, skills)
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
    skills = load_skills(config)

    if args.config_only:
        kubectl_apply_configmap_only(config, prompt, skills)
    elif args.run:
        kubectl_run(config, prompt)
    elif args.apply:
        # Delete the manual Job before applying — its pod template spec is immutable,
        # so kubectl apply fails if the image tag changed. --run handles this already;
        # mirror that behaviour here.
        if config["trigger"]["kind"] == "cron":
            job_name = f"{config['name']}-manual"
            subprocess.run(
                ["kubectl", "delete", "job", job_name, "-n", NAMESPACE, "--ignore-not-found"],
                check=True,
            )
        manifests = render_manifests(config, prompt, skills)
        kubectl_apply(manifests)
    else:
        # Default: dry-run / print
        manifests = render_manifests(config, prompt, skills)
        print(manifests)


if __name__ == "__main__":
    main()
