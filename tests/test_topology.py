"""Tests for the demo multi-provider topology + per-provider seed slicing."""

from __future__ import annotations

import importlib
import os
import re
import sys

import pytest

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "demo", "lib")
sys.path.insert(0, _LIB)

import datasets  # noqa: E402
import seed  # noqa: E402
import topology  # noqa: E402


def test_one_node_per_logical_provider():
    nodes = topology.provider_nodes()
    assert [n.id for n in nodes] == [p.id for p in datasets.PROVIDERS]


def test_ports_are_unique_and_avoid_consumer_block():
    nodes = topology.provider_nodes()
    ports = topology.all_ports()
    # No duplicate ports across all connectors.
    assert len(ports) == len(set(ports))
    # The consumer reserves the 29xxx block — no provider may land there.
    assert not any(29000 <= p < 30000 for p in ports), f"provider port in consumer block: {ports}"
    # Provider 0 keeps the original 19xxx block (back-compat with integration tests).
    assert nodes[0].mgmt_port == 19193
    assert nodes[0].protocol_port == 19194


def test_default_providers_match_seed_targets():
    providers = topology.default_providers()
    targets = topology.seed_targets()
    assert [p["id"] for p in providers] == [t["id"] for t in targets]
    assert all(p["dsp"].startswith("http") for p in providers)
    assert all(t["mgmt"].endswith("/management") for t in targets)


def test_seed_assets_for_single_provider_is_a_strict_slice():
    first = datasets.PROVIDERS[0]
    sliced = seed.assets_for(first.id)
    # Only this provider's datasets, and fewer than the full catalogue.
    expected = {datasets.asset_id(first, d) for d in first.datasets}
    assert {a["id"] for a in sliced} == expected
    assert len(sliced) < len(seed.assets_for(None))


def test_seed_assets_for_none_returns_every_dataset():
    all_assets = seed.assets_for(None)
    total = sum(len(p.datasets) for p in datasets.PROVIDERS)
    assert len(all_assets) == total


def test_seed_targets_default_is_single_legacy_target(monkeypatch):
    monkeypatch.delenv("PYTHIA_SEED_TARGETS", raising=False)
    importlib.reload(seed)
    targets = seed._targets()
    assert targets == [{"id": None, "mgmt": seed.PROVIDER_MANAGEMENT}]


def test_seed_targets_parsed_from_env(monkeypatch):
    monkeypatch.setenv(
        "PYTHIA_SEED_TARGETS",
        '[{"id":"a","mgmt":"http://x/management"},{"id":"b","mgmt":"http://y/management"}]',
    )
    targets = seed._targets()
    assert [t["id"] for t in targets] == ["a", "b"]


def test_kupferwerk_dataset_asset_ids_match_ttl_preg_asset_id():
    """A downstream consumer cross-checks the EDC asset id against the
    `preg:assetId` literal each kupferwerk .ttl file declares for itself — they
    must be equal, and the asset must actually be published as Turtle.

    Parsed with a plain regex rather than rdflib, which isn't a dependency here.
    """
    provider = next(p for p in datasets.PROVIDERS if p.id == "kupferwerk")
    data_dir = os.path.join(_LIB, "data")
    assert provider.datasets, "kupferwerk should publish at least one dataset"
    for dataset in provider.datasets:
        assert dataset.content_type == "text/turtle"
        assert dataset.file_name is not None
        ttl_path = os.path.join(data_dir, dataset.file_name)
        with open(ttl_path, encoding="utf-8") as f:
            ttl = f.read()
        match = re.search(r'preg:assetId\s+"([^"]+)"', ttl)
        assert match, f"no preg:assetId literal found in {dataset.file_name}"
        assert datasets.asset_id(provider, dataset) == match.group(1)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
