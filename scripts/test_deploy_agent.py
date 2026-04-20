"""
Unit tests for deploy_agent.py — Phase A-4.

Covers:
- Legacy shim normalisation (type:cron, absent-type, absent-everything)
- render_* dispatch by trigger.kind
- Label stamping
- Golden-manifest parity for daily-briefing
"""

import sys
import warnings
from pathlib import Path

import pytest
import yaml

# Ensure the scripts directory is importable when running from the repo root.
SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import deploy_agent as da


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_agent(tmp_path: Path, agent_yaml: str, prompt: str = "# prompt\n") -> Path:
    """Write prompts/<name>/agent.yaml + AGENT.md under tmp_path and return tmp_path.

    The agent name is extracted from the agent_yaml content.  Callers should
    monkeypatch da.REPO_ROOT to tmp_path so load_config() reads from there.
    """
    import yaml as _yaml
    raw = _yaml.safe_load(agent_yaml)
    name = raw["name"]
    agent_dir = tmp_path / "prompts" / name
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text(agent_yaml)
    (agent_dir / "AGENT.md").write_text(prompt)
    return tmp_path


def _make_config(raw: dict) -> dict:
    """Build a fully-shimmed config by writing a temp agent and calling load_config.

    Writes only the caller-supplied fields (not DEFAULTS) to agent.yaml so that
    load_config applies DEFAULTS internally — exactly as production does.
    This helper creates a throwaway tmp directory so tests that don't have
    tmp_path as a fixture parameter can still get a valid config dict.
    """
    import tempfile, yaml as _yaml, warnings as _w
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        name = raw.get("name", "test-agent")
        agent_dir = tmp / "prompts" / name
        agent_dir.mkdir(parents=True)
        # Write only the raw dict — load_config merges DEFAULTS itself.
        (agent_dir / "agent.yaml").write_text(_yaml.dump(raw))
        (agent_dir / "AGENT.md").write_text("# prompt\n")
        original = da.REPO_ROOT
        da.REPO_ROOT = tmp
        try:
            with _w.catch_warnings():
                _w.simplefilter("ignore")
                config, _ = da.load_config(name)
        finally:
            da.REPO_ROOT = original
    return config


# ---------------------------------------------------------------------------
# Legacy shim tests
# ---------------------------------------------------------------------------

def test_legacy_shim_type_cron_without_schedule_errors(tmp_path, monkeypatch):
    """type:cron with no cron.schedule → SystemExit with an error message."""
    _write_agent(tmp_path, "name: no-sched-agent\ntype: cron\n")
    monkeypatch.setattr(da, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit):
        da.load_config("no-sched-agent")


def test_legacy_shim_type_cron(tmp_path, monkeypatch):
    """type:cron + top-level cron: block → canonical trigger block."""
    _write_agent(tmp_path, (
        "name: test-agent\n"
        "type: cron\n"
        "cron:\n"
        "  schedule: '0 5 * * *'\n"
        "  timezone: America/Denver\n"
        "  activeDeadlineSeconds: 900\n"
        "  backoffLimit: 2\n"
    ))
    monkeypatch.setattr(da, "REPO_ROOT", tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config, _ = da.load_config("test-agent")

    assert config["trigger"]["kind"] == "cron"
    assert config["trigger"]["runtime"]["timezone"] == "America/Denver"
    assert config["trigger"]["runtime"]["activeDeadlineSeconds"] == 900
    assert config["trigger"]["runtime"]["backoffLimit"] == 2
    assert config["trigger"]["cron"]["schedule"] == "0 5 * * *"


def test_legacy_shim_absent_type(tmp_path, monkeypatch):
    """No type: field but top-level cron: present → trigger.kind == cron."""
    _write_agent(tmp_path, (
        "name: test-agent\n"
        "cron:\n"
        "  schedule: '30 8 * * 1-5'\n"
        "  timezone: UTC\n"
    ))
    monkeypatch.setattr(da, "REPO_ROOT", tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config, _ = da.load_config("test-agent")

    assert config["trigger"]["kind"] == "cron"
    assert config["trigger"]["cron"]["schedule"] == "30 8 * * 1-5"


def test_legacy_shim_absent_everything(tmp_path, monkeypatch):
    """Bare config with only name: → trigger.kind == manual."""
    # Verify DEFAULTS has no cron.schedule (sanity check).
    assert not da.DEFAULTS.get("cron", {}).get("schedule"), (
        "DEFAULTS should not have a cron.schedule — check DEFAULTS dict"
    )
    _write_agent(tmp_path, "name: test-manual-agent\n")
    monkeypatch.setattr(da, "REPO_ROOT", tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        config, _ = da.load_config("test-manual-agent")
    assert config["trigger"]["kind"] == "manual"


def test_legacy_shim_emits_deprecation_warning(tmp_path, monkeypatch):
    """Legacy cron: block triggers a DeprecationWarning via load_config."""
    _write_agent(tmp_path, (
        "name: warn-agent\n"
        "cron:\n"
        "  schedule: '0 1 * * *'\n"
    ))
    monkeypatch.setattr(da, "REPO_ROOT", tmp_path)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        da.load_config("warn-agent")
    assert any(issubclass(warning.category, DeprecationWarning) for warning in w), (
        "Expected a DeprecationWarning from load_config for legacy cron: block"
    )


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------

MINIMAL_PROMPT = "# Test Agent\nDo stuff."


def test_dispatch_cron_emits_cronjob_and_manual_job():
    """Rendering a cron config yields exactly 1 ConfigMap + 1 CronJob + 1 Job."""
    config = _make_config({
        "name": "cron-agent",
        "cron": {"schedule": "0 5 * * *"},
    })
    docs = da.render_cron(config, MINIMAL_PROMPT)

    kinds = [d["kind"] for d in docs]
    assert kinds.count("ConfigMap") == 1
    assert kinds.count("CronJob") == 1
    assert kinds.count("Job") == 1
    assert len(docs) == 3


def test_dispatch_manual_emits_configmap_only():
    """manual trigger → only ConfigMap emitted."""
    config = _make_config({"name": "manual-agent"})
    # Force kind to manual (absent-everything path already does this).
    assert config["trigger"]["kind"] == "manual"
    docs = da.render_manual(config, MINIMAL_PROMPT)
    assert len(docs) == 1
    assert docs[0]["kind"] == "ConfigMap"


def test_dispatch_https_stub_emits_configmap():
    """render_https stub returns exactly 1 ConfigMap with AGENT.md and mcp.json; does not raise."""
    config = _make_config({"name": "https-agent"})
    config["trigger"] = {
        "kind": "https",
        "runtime": {"timezone": "UTC", "activeDeadlineSeconds": 1800, "backoffLimit": 1},
    }
    docs = da.render_https(config, MINIMAL_PROMPT)
    assert len(docs) == 2  # ConfigMap + stub placeholder
    configmap = next(d for d in docs if d["kind"] == "ConfigMap" and not d["metadata"]["name"].endswith("-https-stub"))
    assert "AGENT.md" in configmap["data"]
    assert "mcp.json" in configmap["data"]


def test_dispatch_queue_stub_emits_configmap():
    """render_queue stub returns exactly 1 ConfigMap with AGENT.md and mcp.json; does not raise."""
    config = _make_config({"name": "queue-agent"})
    config["trigger"] = {
        "kind": "queue",
        "runtime": {"timezone": "UTC", "activeDeadlineSeconds": 1800, "backoffLimit": 1},
    }
    docs = da.render_queue(config, MINIMAL_PROMPT)
    assert len(docs) == 2  # ConfigMap + stub placeholder
    configmap = next(d for d in docs if d["kind"] == "ConfigMap" and not d["metadata"]["name"].endswith("-queue-stub"))
    assert "AGENT.md" in configmap["data"]
    assert "mcp.json" in configmap["data"]


# ---------------------------------------------------------------------------
# Label-stamping tests
# ---------------------------------------------------------------------------

def test_labels_stamped():
    """Rendered cron manifests include agent, trigger-kind, managed-by=agent-platform labels."""
    config = _make_config({
        "name": "label-agent",
        "cron": {"schedule": "0 5 * * *"},
    })
    docs = da.render_cron(config, MINIMAL_PROMPT)

    required_labels = {
        "agent": "label-agent",
        "trigger-kind": "cron",
        "managed-by": "agent-platform",
    }

    # CronJob and Job should both carry platform labels.
    for doc in docs:
        if doc["kind"] in ("CronJob", "Job"):
            labels = doc["metadata"].get("labels", {})
            for k, v in required_labels.items():
                assert labels.get(k) == v, (
                    f"{doc['kind']} missing label {k}={v}; got labels={labels}"
                )

    # CronJob jobTemplate should also carry the labels.
    cronjob = next(d for d in docs if d["kind"] == "CronJob")
    job_template_labels = (
        cronjob["spec"]["jobTemplate"]["metadata"].get("labels", {})
    )
    for k, v in required_labels.items():
        assert job_template_labels.get(k) == v, (
            f"CronJob jobTemplate missing label {k}={v}; got {job_template_labels}"
        )


# ---------------------------------------------------------------------------
# Golden-manifest parity test
# ---------------------------------------------------------------------------

GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "golden" / "daily-briefing.yaml"


def test_golden_daily_briefing():
    """Render daily-briefing and compare to the golden fixture byte-for-byte."""
    assert GOLDEN_PATH.exists(), (
        f"Golden fixture not found: {GOLDEN_PATH}. "
        "Run: uv run python scripts/deploy_agent.py daily-briefing > tests/fixtures/golden/daily-briefing.yaml"
    )

    config, prompt = da.load_config("daily-briefing")
    skills = da.load_skills(config)
    rendered = da.render_manifests(config, prompt, skills, include_storage=True)

    golden = GOLDEN_PATH.read_text()

    assert rendered == golden, (
        "Rendered manifests differ from golden fixture.\n"
        "If this change is intentional, regenerate the fixture:\n"
        "  uv run python scripts/deploy_agent.py daily-briefing "
        "> tests/fixtures/golden/daily-briefing.yaml"
    )
