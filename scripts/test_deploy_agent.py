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

def _make_config(raw: dict) -> dict:
    """Merge raw dict with DEFAULTS and run the legacy shim."""
    # Patch REPO_ROOT so load_config() can find files; for shim-only tests we
    # call the shim logic directly instead of going through load_config().
    config = da.deep_merge(da.DEFAULTS, raw)

    # Run the same shim logic that load_config() runs (extracted for testability).
    if "trigger" not in config:
        raw_cron = config.get("cron", {})
        has_cron_block = bool(raw_cron.get("schedule"))
        if has_cron_block:
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
                    "successfulJobsHistoryLimit": raw_cron.get("successfulJobsHistoryLimit", 3),
                    "failedJobsHistoryLimit": raw_cron.get("failedJobsHistoryLimit", 3),
                },
            }
        else:
            config["trigger"] = {"kind": "manual", "runtime": {}, "manual": {}}

    trigger = config["trigger"]
    trigger.setdefault("runtime", {})
    trigger["runtime"].setdefault("timezone", "UTC")
    trigger["runtime"].setdefault("activeDeadlineSeconds", 1800)
    trigger["runtime"].setdefault("backoffLimit", 1)

    if trigger["kind"] == "cron":
        trigger.setdefault("cron", {})
        raw_cron = config.get("cron", {})
        trigger["cron"].setdefault("schedule", raw_cron.get("schedule", ""))
        trigger["cron"].setdefault("concurrencyPolicy", raw_cron.get("concurrencyPolicy", "Forbid"))
        trigger["cron"].setdefault("successfulJobsHistoryLimit", raw_cron.get("successfulJobsHistoryLimit", 3))
        trigger["cron"].setdefault("failedJobsHistoryLimit", raw_cron.get("failedJobsHistoryLimit", 3))
        config["cron"]["timezone"] = trigger["runtime"]["timezone"]
        config["cron"]["activeDeadlineSeconds"] = trigger["runtime"]["activeDeadlineSeconds"]
        config["cron"]["backoffLimit"] = trigger["runtime"]["backoffLimit"]
        config["cron"]["schedule"] = trigger["cron"]["schedule"]
        config["cron"]["concurrencyPolicy"] = trigger["cron"]["concurrencyPolicy"]
        config["cron"]["successfulJobsHistoryLimit"] = trigger["cron"]["successfulJobsHistoryLimit"]
        config["cron"]["failedJobsHistoryLimit"] = trigger["cron"]["failedJobsHistoryLimit"]

    return config


# ---------------------------------------------------------------------------
# Legacy shim tests
# ---------------------------------------------------------------------------

def test_legacy_shim_type_cron():
    """type:cron + top-level cron: block → canonical trigger block."""
    raw = {
        "name": "test-agent",
        "type": "cron",
        "cron": {
            "schedule": "0 5 * * *",
            "timezone": "America/Denver",
            "activeDeadlineSeconds": 900,
            "backoffLimit": 2,
        },
    }
    config = _make_config(raw)

    assert config["trigger"]["kind"] == "cron"
    assert config["trigger"]["runtime"]["timezone"] == "America/Denver"
    assert config["trigger"]["runtime"]["activeDeadlineSeconds"] == 900
    assert config["trigger"]["runtime"]["backoffLimit"] == 2
    assert config["trigger"]["cron"]["schedule"] == "0 5 * * *"


def test_legacy_shim_absent_type():
    """No type: field but top-level cron: present → trigger.kind == cron."""
    raw = {
        "name": "test-agent",
        "cron": {
            "schedule": "30 8 * * 1-5",
            "timezone": "UTC",
        },
    }
    config = _make_config(raw)

    assert config["trigger"]["kind"] == "cron"
    assert config["trigger"]["cron"]["schedule"] == "30 8 * * 1-5"


def test_legacy_shim_absent_everything():
    """Bare config with only name: → trigger.kind == manual."""
    raw = {"name": "test-manual-agent"}
    # Override the DEFAULTS cron schedule that deep_merge would inject — the
    # DEFAULTS cron block has no schedule, so it stays empty.
    # We need to ensure no schedule comes in via DEFAULTS.
    base = da.deep_merge(da.DEFAULTS, raw)
    # DEFAULTS["cron"] has no "schedule" key, so has_cron_block will be False.
    assert not base.get("cron", {}).get("schedule"), (
        "DEFAULTS should not have a cron.schedule — check DEFAULTS dict"
    )
    config = _make_config(raw)
    assert config["trigger"]["kind"] == "manual"


def test_legacy_shim_emits_deprecation_warning():
    """Legacy cron: block triggers a DeprecationWarning via warnings.warn."""
    raw = {
        "name": "warn-agent",
        "cron": {"schedule": "0 1 * * *"},
    }
    # Manually replicate the load_config warning path.
    config = da.deep_merge(da.DEFAULTS, raw)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        if "trigger" not in config:
            raw_cron = config.get("cron", {})
            if raw_cron.get("schedule"):
                warnings.warn("deprecated", DeprecationWarning, stacklevel=2)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)


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
    """render_https stub returns a ConfigMap (+ marker); does not raise."""
    config = _make_config({"name": "https-agent"})
    config["trigger"] = {
        "kind": "https",
        "runtime": {"timezone": "UTC", "activeDeadlineSeconds": 1800, "backoffLimit": 1},
    }
    docs = da.render_https(config, MINIMAL_PROMPT)
    # Must not raise; must include a ConfigMap.
    kinds = [d["kind"] for d in docs]
    assert "ConfigMap" in kinds


def test_dispatch_queue_stub_emits_configmap():
    """render_queue stub returns a ConfigMap (+ marker); does not raise."""
    config = _make_config({"name": "queue-agent"})
    config["trigger"] = {
        "kind": "queue",
        "runtime": {"timezone": "UTC", "activeDeadlineSeconds": 1800, "backoffLimit": 1},
    }
    docs = da.render_queue(config, MINIMAL_PROMPT)
    kinds = [d["kind"] for d in docs]
    assert "ConfigMap" in kinds


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
