#!/usr/bin/env python3
"""Local multi-provider topology for the Pythia demo.

The demo launches ONE EDC connector per logical provider defined in
``datasets.PROVIDERS`` — so "how many providers" is derived from the data, not
hardcoded. Each connector gets a distinct EDC participant id (matching the
logical provider id) and its own non-overlapping port block.

Single source of truth consumed by:

  - up.sh       launches one JAR per provider (ports + generated config)
  - seed.py     seeds each provider connector with only its own datasets
  - ask.py      default provider fan-out list when PYTHIA_PROVIDERS is unset
  - conftest.py live-test provider list

Port scheme (per provider index, consumer reserves the ``2`` block):

    index 0 → prefix 1 → 19191/19192/19193/19194/19291   (api/control/mgmt/dsp/public)
    index 1 → prefix 3 → 39191/.../39291
    index 2 → prefix 4 → 49191/.../49291
    ...      (consumer connector keeps the 29191–29291 block)

Provider 0 keeps the original 19xxx block so existing single-provider tooling
and the integration tests continue to address it unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datasets

# DSP version suffix the consumer's management API needs on counterPartyAddress
# (edc.dsp.callback.address is the base; the management call needs the versioned path).
DSP_VERSION = os.environ.get("PYTHIA_DSP_VERSION", "2025-1")

# Port block reserved for the consumer connector — never assigned to a provider.
_CONSUMER_PREFIX = 2


def _prefix(index: int) -> int:
    """Map a provider index to its port-block prefix, skipping the consumer's block."""
    prefix = 1 if index == 0 else index + 2  # 0→1, 1→3, 2→4, 3→5, ...
    if prefix == _CONSUMER_PREFIX:
        raise ValueError(f"provider index {index} collides with the consumer port block")
    return prefix


@dataclass(frozen=True)
class ProviderNode:
    """One physical EDC provider connector and the datasets it hosts."""

    id: str
    name: str
    prefix: int
    dataset_ids: tuple[str, ...]

    @property
    def api_port(self) -> int:
        return self.prefix * 10000 + 9191

    @property
    def control_port(self) -> int:
        return self.prefix * 10000 + 9192

    @property
    def mgmt_port(self) -> int:
        return self.prefix * 10000 + 9193

    @property
    def protocol_port(self) -> int:
        return self.prefix * 10000 + 9194

    @property
    def public_port(self) -> int:
        return self.prefix * 10000 + 9291

    @property
    def mgmt_url(self) -> str:
        return f"http://localhost:{self.mgmt_port}/management"

    @property
    def dsp_url(self) -> str:
        return f"http://localhost:{self.protocol_port}/protocol/{DSP_VERSION}"

    @property
    def public_endpoint(self) -> str:
        return f"http://localhost:{self.public_port}/public"


def provider_nodes() -> list[ProviderNode]:
    """One ProviderNode per logical provider in datasets.PROVIDERS, in order."""
    nodes: list[ProviderNode] = []
    for i, provider in enumerate(datasets.PROVIDERS):
        nodes.append(
            ProviderNode(
                id=provider.id,
                name=provider.name,
                prefix=_prefix(i),
                dataset_ids=tuple(d.id for d in provider.datasets),
            )
        )
    return nodes


def default_providers() -> list[dict]:
    """Provider fan-out list for the consumer ({dsp, id} per provider)."""
    return [{"dsp": n.dsp_url, "id": n.id} for n in provider_nodes()]


def seed_targets() -> list[dict]:
    """Per-provider seed targets ({id, mgmt} per provider)."""
    return [{"id": n.id, "mgmt": n.mgmt_url} for n in provider_nodes()]


def all_ports() -> list[int]:
    """Every TCP port used by all provider connectors (for the kill/clear loop)."""
    ports: list[int] = []
    for n in provider_nodes():
        ports += [n.api_port, n.control_port, n.mgmt_port, n.protocol_port, n.public_port]
    return ports


# ── CLI: emit topology in shapes bash / env vars can consume ────────────────────

def _print_launch() -> None:
    """One line per provider: id|api|control|mgmt|protocol|public|mgmt_url"""
    for n in provider_nodes():
        print(
            f"{n.id}|{n.api_port}|{n.control_port}|{n.mgmt_port}"
            f"|{n.protocol_port}|{n.public_port}|{n.mgmt_url}"
        )


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "--print-launch"
    if mode == "--print-launch":
        _print_launch()
    elif mode == "--print-providers-json":
        print(json.dumps(default_providers()))
    elif mode == "--print-seed-targets":
        print(json.dumps(seed_targets()))
    elif mode == "--print-ports":
        print(" ".join(str(p) for p in all_ports()))
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
