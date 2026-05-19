"""
Tests for the trust-slice: SHACL validation of ODRL policy offers.

Good offer fixture is based on the real EDC catalog offer structure
used throughout test_catalog.py and test_ask.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pythia.errors import TrustError
from pythia.trust import validate_offer

# ── fixtures ───────────────────────────────────────────────────────────────────

GOOD_OFFER = {
    "@id": "offer:abc:asset-co2-2023",
    "assigner": "provider",
    "target": "asset-co2-2023",
}

BAD_OFFER_MISSING_TARGET = {
    "@id": "offer:abc:malformed",
    "assigner": "provider",
    # target intentionally absent
}

BAD_OFFER_MISSING_ID = {
    # @id intentionally absent — not a valid named resource
    "target": "asset-co2-2023",
}

# ── validate_offer unit tests ──────────────────────────────────────────────────


def test_valid_offer_passes():
    """Well-formed offer with @id and target does not raise."""
    validate_offer(GOOD_OFFER)


def test_missing_target_raises_trust_error():
    """Offer without target raises TrustError."""
    with pytest.raises(TrustError, match="target"):
        validate_offer(BAD_OFFER_MISSING_TARGET)


def test_missing_id_raises_trust_error():
    """Offer without @id raises TrustError."""
    with pytest.raises(TrustError):
        validate_offer(BAD_OFFER_MISSING_ID)


def test_trust_error_is_pythia_error():
    """TrustError is a subclass of PythiaError."""
    from pythia.errors import PythiaError
    assert issubclass(TrustError, PythiaError)


def test_trust_error_message_contains_violation():
    """TrustError message includes a human-readable violation summary."""
    with pytest.raises(TrustError) as exc_info:
        validate_offer(BAD_OFFER_MISSING_TARGET)
    assert str(exc_info.value)


# ── AskController integration: verify_trust=True blocks bad offer ──────────────


@pytest.mark.asyncio
async def test_ask_verify_trust_skips_malformed_offer():
    """
    When verify_trust=True and the top-ranked offer is malformed,
    no POST to /contractnegotiations is made and ask() returns None.
    """
    from pythia.ask import AskController
    from pythia.models import Catalog, CatalogAsset

    bad_raw = {
        "@id": "offer:malformed",
        # target missing — will fail SHACL
        "assigner": "provider",
    }
    bad_asset_raw = {
        "@id": "asset-only",
        "title": "CO2 Emissions 2023",
        "description": "Annual CO2 data for German automotive suppliers",
        "hasPolicy": [bad_raw],
    }
    bad_asset = CatalogAsset.from_dcat(bad_asset_raw)
    bad_asset.offers[0].raw.update(bad_raw)
    # Simulate offer with no resolvable target (raw has no target, and offer.target is None)
    bad_asset.offers[0].target = None

    catalog = Catalog(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
        assets=[bad_asset],
    )

    ds = MagicMock()
    mock_catalog_ctrl = MagicMock()
    mock_catalog_ctrl.query = AsyncMock(return_value=catalog)
    ds.catalog = mock_catalog_ctrl
    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b"data")

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "CO2 emissions German automotive",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider"}],
        top_k=1,
        verify_trust=True,
        raw=True,
    )

    assert result is None
    ds.negotiate.assert_not_called()


# ── Real EDC offer shape (no "target" key — target injected via param) ─────────

REAL_EDC_OFFER = {
    "@id": "contract:asset:uuid",
    "@type": "Offer",
}


def test_real_edc_offer_with_target_param_passes():
    """Real EDC offer shape (no target key) passes when target is supplied as param."""
    validate_offer(REAL_EDC_OFFER, target="rheinmobil_co2_oem_2023")


def test_real_edc_offer_without_target_param_raises():
    """Real EDC offer shape (no target key) raises TrustError when no target supplied."""
    with pytest.raises(TrustError):
        validate_offer(REAL_EDC_OFFER)


# ── Context merge: existing @context is preserved, not overwritten ─────────────


def test_existing_context_is_merged_not_overwritten():
    """Offer with an existing @context dict gets _ODRL_CONTEXT merged in, not replaced."""
    offer_with_context = {
        "@id": "offer:abc:asset-co2-2023",
        "@context": {"custom_key": "http://example.org/custom"},
        "target": "asset-co2-2023",
    }
    validate_offer(offer_with_context)


def test_remote_string_context_is_not_dereferenced():
    """A provider-supplied remote @context URL must not be fetched (SSRF/DoS/bypass).

    The offer comes from an attacker-controlled catalog. If the JSON-LD parser
    dereferenced the remote context we'd see an outbound request to the closed
    port (a connection error). Instead validation must run against the local
    ODRL context only, so a well-formed offer still passes without any network.
    """
    offer_with_remote_context = {
        "@id": "offer:abc:asset-co2-2023",
        # port 9 (discard) is closed locally; a fetch attempt would raise.
        "@context": "http://127.0.0.1:9/evil-context.jsonld",
        "target": "asset-co2-2023",
    }
    validate_offer(offer_with_remote_context)  # must not raise / must not fetch


@pytest.mark.asyncio
async def test_ask_verify_trust_false_does_not_validate():
    """When verify_trust=False (default), malformed offers still proceed to negotiate."""
    from pythia.ask import AskController
    from pythia.models import Catalog, CatalogAsset

    bad_raw = {
        "@id": "offer:malformed",
        "assigner": "provider",
        # target missing — would fail SHACL if checked
    }
    bad_asset_raw = {
        "@id": "asset-only",
        "title": "CO2 Emissions 2023",
        "description": "Annual CO2 data for German automotive suppliers",
        "hasPolicy": [bad_raw],
    }
    bad_asset = CatalogAsset.from_dcat(bad_asset_raw)
    bad_asset.offers[0].raw.update(bad_raw)

    catalog = Catalog(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
        assets=[bad_asset],
    )

    ds = MagicMock()
    mock_catalog_ctrl = MagicMock()
    mock_catalog_ctrl.query = AsyncMock(return_value=catalog)
    ds.catalog = mock_catalog_ctrl
    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b"data")

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "CO2 emissions German automotive",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider"}],
        top_k=1,
        verify_trust=False,
        raw=True,
    )

    assert result == b"data"
    ds.negotiate.assert_called_once()
