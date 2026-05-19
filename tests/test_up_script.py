"""Tests for scripts/demo/up.sh toggle-resolution logic (via DRY_RUN plan mode)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_UP_SH = _REPO_ROOT / "scripts" / "demo" / "up.sh"

_CLEARED_KEYS = (
    "START_LOCAL_CONNECTORS",
    "START_PROVIDER",
    "START_CONSUMER",
    "START_MOCK_SERVER",
    "SEED_PROVIDER",
    "WAIT_FOR_PROVIDER",
    "WAIT_FOR_CONSUMER",
    "CONSUMER_ONLY",
    "PROVIDER_MGMT",
    "CONSUMER_MGMT",
)

_PLAN_RE = re.compile(r"^\[plan\] (\w+)=(.*)$")


def _run_plan(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in _CLEARED_KEYS:
        env.pop(key, None)
    env["DRY_RUN"] = "1"
    env.update(overrides)
    result = subprocess.run(
        ["bash", str(_UP_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    plan: dict[str, str] = {}
    for line in result.stdout.splitlines():
        m = _PLAN_RE.match(line)
        if m:
            plan[m.group(1)] = m.group(2)
    return plan


def test_default_mode_starts_full_local_stack():
    plan = _run_plan()
    assert plan["START_PROVIDER"] == "1"
    assert plan["START_CONSUMER"] == "1"
    assert plan["START_MOCK_SERVER"] == "1"
    assert plan["SEED_PROVIDER"] == "1"
    assert plan["WAIT_FOR_PROVIDER"] == "1"
    assert plan["WAIT_FOR_CONSUMER"] == "1"


def test_remote_mode_starts_no_local_connectors():
    plan = _run_plan(START_LOCAL_CONNECTORS="0")
    assert plan["START_PROVIDER"] == "0"
    assert plan["START_CONSUMER"] == "0"
    assert plan["START_MOCK_SERVER"] == "1"
    assert plan["SEED_PROVIDER"] == "1"
    assert plan["WAIT_FOR_PROVIDER"] == "1"
    assert plan["WAIT_FOR_CONSUMER"] == "1"


def test_consumer_only_profile():
    plan = _run_plan(CONSUMER_ONLY="1")
    assert plan["START_PROVIDER"] == "0"
    assert plan["START_CONSUMER"] == "1"
    assert plan["START_MOCK_SERVER"] == "0"
    assert plan["SEED_PROVIDER"] == "0"
    assert plan["WAIT_FOR_PROVIDER"] == "0"
    assert plan["WAIT_FOR_CONSUMER"] == "1"


def test_consumer_only_overrides_granular():
    plan = _run_plan(CONSUMER_ONLY="1", START_PROVIDER="1")
    assert plan["START_PROVIDER"] == "0"


def test_granular_provider_off():
    plan = _run_plan(START_PROVIDER="0")
    assert plan["START_PROVIDER"] == "0"
    assert plan["START_CONSUMER"] == "1"


def test_mgmt_urls_passthrough():
    plan = _run_plan(
        PROVIDER_MGMT="http://remote-provider:9999/management",
        CONSUMER_MGMT="http://remote-consumer:8888/management",
    )
    assert plan["PROVIDER_MGMT"] == "http://remote-provider:9999/management"
    assert plan["CONSUMER_MGMT"] == "http://remote-consumer:8888/management"
