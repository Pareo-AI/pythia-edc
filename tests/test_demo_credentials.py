"""Offline tests for the demo credential wiring (scripts/demo/lib/credentials.py).

The live EDC stack is not exercised here. We validate that the demo mints VCs
that the library's trust gate accepts (the CA-signed providers) and rejects (the
intentionally-untrusted DonauTech, signed by a rogue issuer), and that an
AskController driven by the demo credential map skips the untrusted provider but
proceeds for a trusted one. Mocking style mirrors tests/test_ask_credential.py.
"""

from __future__ import annotations

import os
import sys

import pytest

from pythia.ask import AskController
from pythia.credential import verify_credential
from pythia.credential_source import StaticCredentialSource
from pythia.errors import CredentialError
from pythia.models import Catalog, CatalogAsset

# Import the demo credentials module the way demo siblings import each other.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "demo", "lib")
)
import credentials  # noqa: E402

# ── catalog / ds helpers (mirrors test_ask_credential.py) ───────────────────────


def _good_asset(asset_id: str = "asset-co2-2023") -> CatalogAsset:
    raw = {
        "@id": asset_id,
        "title": "CO2 Emissions 2023",
        "description": "Annual CO2 data for German automotive suppliers",
        "hasPolicy": [
            {
                "@id": f"offer:{asset_id}",
                "assigner": "provider",
                "target": asset_id,
            }
        ],
    }
    asset = CatalogAsset.from_dcat(raw)
    asset.offers[0].raw.update(raw["hasPolicy"][0])
    asset.offers[0].target = asset_id
    return asset


def _catalog(provider_id: str, assets: list[CatalogAsset]) -> Catalog:
    return Catalog(
        provider_dsp=f"http://{provider_id}:19194/protocol",
        provider_id=provider_id,
        assets=assets,
    )


def _ds(catalog: Catalog):
    from unittest.mock import AsyncMock, MagicMock

    ds = MagicMock()
    mock_catalog_ctrl = MagicMock()
    mock_catalog_ctrl.query = AsyncMock(return_value=catalog)
    ds.catalog = mock_catalog_ctrl
    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b"data")
    return ds


def _providers(provider_id: str) -> list[dict]:
    return [{"dsp": f"http://{provider_id}:19194/protocol", "id": provider_id}]


QUERY = "CO2 emissions German automotive"


# ── credential map / trust list ─────────────────────────────────────────────────


def test_credential_map_covers_all_demo_providers():
    cmap = credentials.credential_map()
    assert set(cmap) == {"rheinmobil", "zugspitze", "donautech"}


def test_trusted_providers_verify():
    cmap = credentials.credential_map()
    tl = credentials.trust_list()
    # Should not raise.
    verify_credential(cmap["rheinmobil"], trust_list=tl)
    verify_credential(cmap["zugspitze"], trust_list=tl)


def test_donautech_is_untrusted_issuer():
    cmap = credentials.credential_map()
    tl = credentials.trust_list()
    with pytest.raises(CredentialError) as exc:
        verify_credential(cmap["donautech"], trust_list=tl)
    constraints = [f.constraint for f in exc.value.failures]
    assert "UntrustedIssuer" in constraints


# ── AskController-level wiring ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_untrusted_provider_is_skipped():
    """DonauTech's rogue-signed VC is rejected, so no negotiation happens."""
    cmap = credentials.credential_map()
    tl = credentials.trust_list()
    catalog = _catalog("donautech", [_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource(cmap))
    result = await ctrl.ask(
        QUERY,
        providers=_providers("donautech"),
        top_k=1,
        verify_trust=True,
        trust_list=tl,
        raw=True,
    )

    assert result is None
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_trusted_provider_proceeds():
    """RheinMobil's CA-signed VC is accepted, so the query negotiates + fetches."""
    cmap = credentials.credential_map()
    tl = credentials.trust_list()
    catalog = _catalog("rheinmobil", [_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource(cmap))
    result = await ctrl.ask(
        QUERY,
        providers=_providers("rheinmobil"),
        top_k=1,
        verify_trust=True,
        trust_list=tl,
        raw=True,
    )

    assert result == b"data"
    ds.negotiate.assert_called_once()
    ds.fetch.assert_called_once()
