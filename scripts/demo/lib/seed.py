#!/usr/bin/env python3
"""
Seed EDC provider connector(s) with the local CO2 emissions demo datasets.

Two modes:

  - Multi-provider (default for ``./demo up``): set ``PYTHIA_SEED_TARGETS`` to a
    JSON array of ``{"id": ..., "mgmt": ...}`` and each provider connector is
    seeded with ONLY its own datasets (one connector per logical provider).
  - Single-target (legacy / remote): when ``PYTHIA_SEED_TARGETS`` is unset, every
    dataset is seeded to ``PYTHIA_PROVIDER_MGMT_URL`` (one connector hosts all).

Idempotent: 409 Conflict responses are treated as success (already exists).

Run via ``./demo up`` (internal helper: scripts/demo/lib/seed.py).
"""
import json
import os
import sys

import httpx

from pythia.config import TLSConfig

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datasets

PROVIDER_MANAGEMENT = os.environ.get("PYTHIA_PROVIDER_MGMT_URL", "http://localhost:19193/management")
API_KEY = os.environ.get("PYTHIA_API_KEY", "password")
API_KEY_HEADER = os.environ.get("PYTHIA_API_KEY_HEADER", "X-Api-Key")
HEADERS = {
    API_KEY_HEADER: API_KEY,
    "Content-Type": "application/json",
}
POLICY_ID = "demo-permissive-policy"
CONTRACT_DEF_ID = "demo-contract-all"

# Assets are derived from the single source of truth (one logical provider per
# tenant, all sharing the GHG Protocol scope 1/2/3 schema), served by mock_server.


def _asset(provider, dataset) -> dict:
    return {
        "id": datasets.asset_id(provider, dataset),
        "name": dataset.name,
        "description": dataset.description,
        "url": datasets.asset_url(provider, dataset),
    }


def assets_for(provider_id: str | None) -> list[dict]:
    """Asset definitions for one logical provider, or all when provider_id is None."""
    return [
        _asset(provider, dataset)
        for provider, dataset in datasets.iter_datasets()
        if provider_id is None or provider.id == provider_id
    ]


def ok_or_die(r: httpx.Response, label: str) -> bool:
    """Return True if success or already-exists (409), abort otherwise."""
    if r.status_code in (200, 201, 204):
        print(f"  [OK {r.status_code}] {label}")
        return True
    if r.status_code == 409:
        print(f"  [SKIP 409] {label} already exists")
        return True
    print(f"  [ERROR {r.status_code}] {label}: {r.text[:300]}", file=sys.stderr)
    return False


def seed_asset(client: httpx.Client, mgmt_url: str, asset: dict) -> bool:
    body = {
        "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
        "@id": asset["id"],
        "properties": {
            "name": asset["name"],
            "description": asset["description"],
            "contenttype": "application/json",
        },
        "dataAddress": {
            "type": "HttpData",
            "name": asset["name"],
            "baseUrl": asset["url"],
            "proxyPath": "false",
        },
    }
    r = client.post(f"{mgmt_url}/v3/assets", headers=HEADERS, json=body)
    return ok_or_die(r, f"asset:{asset['id']}")


def seed_policy(client: httpx.Client, mgmt_url: str) -> bool:
    body = {
        "@context": {
            "@vocab": "https://w3id.org/edc/v0.0.1/ns/",
            "odrl": "http://www.w3.org/ns/odrl/2/",
        },
        "@id": POLICY_ID,
        "policy": {
            "@context": "http://www.w3.org/ns/odrl.jsonld",
            "@type": "Set",
            "permission": [],
            "prohibition": [],
            "obligation": [],
        },
    }
    r = client.post(f"{mgmt_url}/v3/policydefinitions", headers=HEADERS, json=body)
    return ok_or_die(r, f"policy:{POLICY_ID}")


def seed_contract_definition(client: httpx.Client, mgmt_url: str) -> bool:
    """One contract definition with empty assetsSelector — matches all assets."""
    body = {
        "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
        "@id": CONTRACT_DEF_ID,
        "accessPolicyId": POLICY_ID,
        "contractPolicyId": POLICY_ID,
        "assetsSelector": [],
    }
    r = client.post(f"{mgmt_url}/v3/contractdefinitions", headers=HEADERS, json=body)
    return ok_or_die(r, f"contractdefinition:{CONTRACT_DEF_ID}")


def seed_one(client: httpx.Client, mgmt_url: str, assets: list[dict]) -> bool:
    """Seed a single connector with the given assets + shared policy + contract def."""
    all_ok = True

    print(f"--- Seeding {len(assets)} asset(s) → {mgmt_url} ---")
    for asset in assets:
        if not seed_asset(client, mgmt_url, asset):
            all_ok = False

    print("  - policy")
    if not seed_policy(client, mgmt_url):
        all_ok = False

    print("  - contract definition")
    if not seed_contract_definition(client, mgmt_url):
        all_ok = False

    return all_ok


def _targets() -> list[dict]:
    """Resolve seed targets: explicit PYTHIA_SEED_TARGETS, else one legacy target.

    Each target is ``{"id": <provider id or None>, "mgmt": <management url>}``.
    id=None means "seed every dataset to this connector" (single-host mode).
    """
    raw = os.environ.get("PYTHIA_SEED_TARGETS", "").strip()
    if raw:
        return json.loads(raw)
    return [{"id": None, "mgmt": PROVIDER_MANAGEMENT}]


def main():
    print("=== Pythia Demo Seed Script ===")
    targets = _targets()
    multi = len(targets) > 1 or (targets and targets[0]["id"] is not None)
    if multi:
        print(f"Multi-provider seed: {len(targets)} connector(s)")
    else:
        print(f"Single-provider seed: {targets[0]['mgmt']}")
    print()

    tls = TLSConfig.from_env()
    seeded_ids: list[str] = []
    all_ok = True
    with httpx.Client(timeout=15.0, **tls.httpx_kwargs()) as client:
        for target in targets:
            assets = assets_for(target.get("id"))
            if not assets:
                print(
                    f"  [WARN] no datasets for provider id {target.get('id')!r} — skipping",
                    file=sys.stderr,
                )
                continue
            if not seed_one(client, target["mgmt"], assets):
                all_ok = False
            seeded_ids += [a["id"] for a in assets]
            print()

    if all_ok:
        print(f"Seed complete. {len(seeded_ids)} asset(s) available across "
              f"{len(targets)} connector(s).")
        print(f"Assets: {', '.join(seeded_ids)}")
        sys.exit(0)
    else:
        print("Seed completed with errors — see above.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
