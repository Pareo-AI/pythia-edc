"""Unit tests for Pydantic models — no EDC required."""

from pythia.models import (
    Catalog,
    CatalogAsset,
    EDRToken,
    NegotiationState,
    TransferState,
)

# ── CatalogAsset.from_dcat ─────────────────────────────────────────────────────

def test_catalog_asset_from_dcat_basic():
    dataset = {
        "@id": "asset-co2-2023",
        "title": "CO2 Emissions Report",
        "description": "Annual CO2 data for German automotive suppliers",
        "hasPolicy": [
            {"@id": "offer:abc:asset-co2-2023", "assigner": "provider", "target": "asset-co2-2023"}
        ],
    }
    asset = CatalogAsset.from_dcat(dataset)
    assert asset.id == "asset-co2-2023"
    assert asset.title == "CO2 Emissions Report"
    assert len(asset.offers) == 1
    assert asset.offers[0].id == "offer:abc:asset-co2-2023"


def test_catalog_asset_single_policy_not_list():
    """hasPolicy may be a dict instead of list."""
    dataset = {
        "@id": "asset-1",
        "hasPolicy": {"@id": "offer:1", "target": "asset-1"},
    }
    asset = CatalogAsset.from_dcat(dataset)
    assert len(asset.offers) == 1


def test_catalog_asset_no_policy():
    dataset = {"@id": "asset-no-offer"}
    asset = CatalogAsset.from_dcat(dataset)
    assert asset.offers == []


def test_catalog_first_offer():
    catalog = Catalog(
        provider_dsp="http://provider:9194/protocol",
        provider_id="provider",
        assets=[
            CatalogAsset.from_dcat({
                "@id": "asset-1",
                "title": "First",
                "hasPolicy": [{"@id": "offer-1", "target": "asset-1"}],
            })
        ],
    )
    result = catalog.first_offer
    assert result is not None
    asset, offer = result
    assert asset.id == "asset-1"
    assert offer.id == "offer-1"


# ── NegotiationState ───────────────────────────────────────────────────────────

def test_negotiation_state_finalized():
    s = NegotiationState(**{"@id": "neg-1"}, state="FINALIZED", contract_agreement_id="agr-1")
    assert s.is_finalized
    assert not s.is_failed
    assert s.is_terminal


def test_negotiation_state_terminated():
    s = NegotiationState(**{"@id": "neg-1"}, state="TERMINATED")
    assert s.is_failed
    assert s.is_terminal
    assert not s.is_finalized


def test_negotiation_state_strips_namespace():
    s = NegotiationState(
        **{"@id": "neg-1"},
        state="https://w3id.org/edc/v0.0.1/ns/FINALIZED",
    )
    assert s.state == "FINALIZED"


def test_negotiation_state_transient():
    for state in ["REQUESTED", "AGREED", "VERIFIED"]:
        s = NegotiationState(**{"@id": "neg-1"}, state=state)
        assert not s.is_terminal


# ── TransferState ──────────────────────────────────────────────────────────────

def test_transfer_state_started():
    s = TransferState(**{"@id": "t-1"}, state="STARTED")
    assert s.is_started
    assert not s.is_failed


def test_transfer_state_terminated():
    s = TransferState(**{"@id": "t-1"}, state="TERMINATED")
    assert s.is_failed
    assert not s.is_started


# ── EDRToken ───────────────────────────────────────────────────────────────────

def test_edr_token_headers():
    token = EDRToken(
        endpoint="http://localhost:19291/public",
        authorization="eyJhbGci.test.token",
    )
    assert token.headers == {"Authorization": "eyJhbGci.test.token"}
