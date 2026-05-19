"""Tests that ./demo loads a config profile (ENV_FILE) and exports it to up.sh."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEMO = _REPO_ROOT / "demo"

_CLEARED_KEYS = (
    "ENV_FILE",
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


def _run_demo_up(env_file: Path | None, **overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in _CLEARED_KEYS:
        env.pop(key, None)
    env["DRY_RUN"] = "1"
    if env_file is not None:
        env["ENV_FILE"] = str(env_file)
    env.update(overrides)
    return subprocess.run(
        ["bash", str(_DEMO), "up"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _plan(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        m = _PLAN_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_profile_values_reach_up_sh(tmp_path):
    profile = tmp_path / "demo.consumer.env"
    profile.write_text("CONSUMER_ONLY=1\nCONSUMER_MGMT=http://remote-consumer:8888/management\n")
    result = _run_demo_up(profile)
    assert result.returncode == 0, result.stderr
    plan = _plan(result.stdout)
    assert plan["START_PROVIDER"] == "0"
    assert plan["START_CONSUMER"] == "1"
    assert plan["SEED_PROVIDER"] == "0"
    assert plan["WAIT_FOR_PROVIDER"] == "0"
    assert plan["CONSUMER_MGMT"] == "http://remote-consumer:8888/management"
    assert "loading config from" in result.stderr


def test_missing_explicit_env_file_warns_but_runs(tmp_path):
    result = _run_demo_up(tmp_path / "does-not-exist.env")
    assert result.returncode == 0, result.stderr
    plan = _plan(result.stdout)
    assert plan["START_PROVIDER"] == "1"  # falls back to defaults
    assert "not found" in result.stderr


def test_inline_env_overrides_still_work_without_profile(tmp_path):
    result = _run_demo_up(None, CONSUMER_ONLY="1")
    assert result.returncode == 0, result.stderr
    plan = _plan(result.stdout)
    assert plan["START_PROVIDER"] == "0"
    assert plan["START_CONSUMER"] == "1"
