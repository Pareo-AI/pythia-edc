"""Test configuration and fixtures."""

import json
import os
import sys

import pytest

# The demo now launches one connector per logical provider (see
# scripts/demo/lib/topology.py). Provider 0 keeps the original 19xxx port block
# and its EDC participant id is its logical id ("rheinmobil"), so the
# integration tests below address it unchanged.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "demo", "lib"),
)
import topology  # noqa: E402

_NODES = topology.provider_nodes()
_PRIMARY = _NODES[0]

# NOTE: counterPartyAddress requires the DSP version suffix /2025-1
#       (edc.dsp.callback.address=http://localhost:19194/protocol is the base,
#        but management API counterPartyAddress needs the versioned path)
PROVIDER_MANAGEMENT = os.environ.get("PYTHIA_PROVIDER_MGMT_URL", _PRIMARY.mgmt_url)
CONSUMER_MANAGEMENT = os.environ.get("PYTHIA_MANAGEMENT_URL", "http://localhost:29193/management")
PROVIDER_DSP = os.environ.get("PYTHIA_PROVIDER_DSP", _PRIMARY.dsp_url)
CONSUMER_DSP = os.environ.get("PYTHIA_CONSUMER_DSP", "http://localhost:29194/protocol/2025-1")
PROVIDER_ID = _PRIMARY.id
CONSUMER_ID = "consumer"
API_KEY = os.environ.get("PYTHIA_API_KEY", "password")
API_KEY_HEADER = os.environ.get("PYTHIA_API_KEY_HEADER", "X-Api-Key")

# Full provider fan-out list for live tests. Honors PYTHIA_PROVIDERS when set,
# otherwise the local demo topology (all logical providers).
_env_providers = os.environ.get("PYTHIA_PROVIDERS", "").strip()
PROVIDERS = json.loads(_env_providers) if _env_providers else topology.default_providers()


@pytest.fixture
def provider_headers():
    return {API_KEY_HEADER: API_KEY, "Content-Type": "application/json"}


@pytest.fixture
def consumer_headers():
    return {API_KEY_HEADER: API_KEY, "Content-Type": "application/json"}
