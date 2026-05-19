"""
Unit tests for AskController and rank_assets.

Tests cover:
- Semantic ranking returns correct top hit
- Paraphrase still scores top hit
- min_score filter removes low-confidence results
- Fan-out across multiple catalogs
- AskController returns None when no providers configured
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import pythia.ask as ask_mod
from pythia.ask import DEFAULT_MIN_SCORE, AskController, RankedAsset, rank_assets
from pythia.models import Catalog, CatalogAsset
from pythia.synthesize import Answer

# ── helpers ───────────────────────────────────────────────────────────────────

def _asset(asset_id: str, title: str, description: str = "") -> CatalogAsset:
    raw = {
        "@id": asset_id,
        "title": title,
        "description": description,
        "hasPolicy": [{"@id": f"offer:{asset_id}", "target": asset_id}],
    }
    return CatalogAsset.from_dcat(raw)


def _catalog(*assets: CatalogAsset, provider_id: str = "provider") -> Catalog:
    return Catalog(
        provider_dsp="http://provider:19194/protocol",
        provider_id=provider_id,
        assets=list(assets),
    )


CATALOG = _catalog(
    _asset("co2-2023", "CO2 Emissions 2023",
           "Annual CO2 emissions data for German automotive suppliers"),
    _asset("svhc-report", "SVHC Substance Report",
           "REACH compliance substance data for EU chemicals"),
    _asset("energy-q1", "Energy Consumption Q1",
           "Quarterly energy usage across manufacturing sites"),
    _asset("gdp-macro", "GDP Macroeconomic Data",
           "EU macroeconomic indicators 2023"),
)


# ── rank_assets ────────────────────────────────────────────────────────────────

def test_top_hit_co2():
    """Direct query hits CO2 asset first."""
    ranked = rank_assets("CO2 emissions German automotive", [CATALOG])
    assert ranked[0].asset.id == "co2-2023"
    assert ranked[0].score > 0.5


def test_top_hit_svhc():
    """SVHC chemical query hits substance report first."""
    ranked = rank_assets("quarterly SVHC chemical substance reports", [CATALOG])
    assert ranked[0].asset.id == "svhc-report"
    assert ranked[0].score > 0.5


def test_paraphrase_co2():
    """Paraphrase 'greenhouse gas output for car makers' still hits CO2."""
    ranked = rank_assets("greenhouse gas output for car makers in Germany", [CATALOG])
    assert ranked[0].asset.id == "co2-2023"


def test_rank_order_descending():
    """Scores are in descending order."""
    ranked = rank_assets("energy manufacturing", [CATALOG])
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_field():
    """rank field is 0-based index."""
    ranked = rank_assets("CO2 emissions", [CATALOG])
    for i, r in enumerate(ranked):
        assert r.rank == i


def test_multi_catalog_fan_out():
    """Results span both catalogs; top hit from correct provider."""
    cat2 = _catalog(
        _asset("weather-2023", "Weather Data 2023", "Meteorological readings Germany"),
        provider_id="provider2",
    )
    ranked = rank_assets("CO2 emissions German automotive", [CATALOG, cat2])
    # co2-2023 still wins
    assert ranked[0].asset.id == "co2-2023"
    assert ranked[0].catalog.provider_id == "provider"


def test_empty_catalog():
    """Empty catalog returns empty list."""
    empty = _catalog()
    ranked = rank_assets("anything", [empty])
    assert ranked == []


def test_no_catalogs():
    """No catalogs returns empty list."""
    ranked = rank_assets("anything", [])
    assert ranked == []


# ── AskController ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ask_no_providers():
    """ask() returns None when no providers configured."""
    ds = MagicMock()
    ds.providers = []
    ctrl = AskController(ds)
    result = await ctrl.ask("CO2 data", providers=[], top_k=1, raw=True)
    assert result is None


@pytest.mark.asyncio
async def test_ask_returns_data_on_success():
    """ask() returns bytes when negotiation and fetch succeed."""
    ds = MagicMock()
    ds.providers = [{"dsp": "http://provider:19194/protocol", "id": "provider"}]

    # catalog returns CATALOG
    mock_catalog_ctrl = MagicMock()
    mock_catalog_ctrl.query = AsyncMock(return_value=CATALOG)
    ds.catalog = mock_catalog_ctrl

    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b'{"value": 42}')

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "CO2 emissions German automotive",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider"}],
        top_k=1,
        min_score=0.0,  # exercise negotiate/fetch, not the relevance gate
        raw=True,
    )
    assert result == b'{"value": 42}'
    ds.negotiate.assert_called_once()
    ds.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_ask_falls_back_to_second_candidate():
    """ask() tries next candidate when first negotiation fails."""
    from pythia.errors import NegotiationError

    ds = MagicMock()
    ds.providers = [{"dsp": "http://provider:19194/protocol", "id": "provider"}]

    mock_catalog_ctrl = MagicMock()
    mock_catalog_ctrl.query = AsyncMock(return_value=CATALOG)
    ds.catalog = mock_catalog_ctrl

    # First attempt fails, second succeeds
    ds.negotiate = AsyncMock(side_effect=[
        NegotiationError("TERMINATED", "neg-1"),
        "agr-002",
    ])
    ds.fetch = AsyncMock(return_value=b"fallback data")

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "CO2 emissions German automotive",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider"}],
        top_k=2,
        min_score=0.0,  # exercise fallback, not the relevance gate
        raw=True,
    )
    assert result == b"fallback data"
    assert ds.negotiate.call_count == 2


# ── relevance threshold (min_score) ─────────────────────────────────────────────

def _ranked_with_score(score: float) -> list[RankedAsset]:
    asset = _asset("x-asset", "X Asset", "some description")
    cat = _catalog(asset)
    return [RankedAsset(score=score, asset=asset, catalog=cat, rank=0)]


def test_default_min_score_is_calibrated():
    """Default threshold separates granite's compressed on/off-topic score bands."""
    assert DEFAULT_MIN_SCORE == 0.82


@pytest.mark.asyncio
async def test_ask_below_threshold_returns_none(monkeypatch):
    """An off-topic query (top score < min_score) returns None without negotiating."""
    ds = MagicMock()
    ds.catalog = MagicMock()
    ds.catalog.query = AsyncMock(return_value=CATALOG)
    ds.negotiate = AsyncMock()
    ds.fetch = AsyncMock()
    monkeypatch.setattr(ask_mod, "rank_assets", lambda q, cats: _ranked_with_score(0.5))

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "best pizza in Naples", providers=[{"dsp": "d", "id": "p"}], top_k=1, raw=True
    )

    assert result is None
    ds.negotiate.assert_not_called()
    ds.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_ask_render_below_threshold_returns_noted_answer(monkeypatch):
    """Render path (default) returns an Answer with an explanatory note, no fetch,
    when nothing clears the threshold."""
    ds = MagicMock()
    ds.catalog = MagicMock()
    ds.catalog.query = AsyncMock(return_value=CATALOG)
    ds.negotiate = AsyncMock()
    ds.fetch = AsyncMock()
    monkeypatch.setattr(ask_mod, "rank_assets", lambda q, cats: _ranked_with_score(0.5))

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "best pizza in Naples", providers=[{"dsp": "d", "id": "p"}], top_k=1
    )

    assert isinstance(result, Answer)
    assert result.table == []
    assert result.note and "threshold" in result.note
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_ask_explicit_min_score_allows_low_match(monkeypatch):
    """An explicit low min_score overrides the default and lets a weak match through."""
    ds = MagicMock()
    ds.catalog = MagicMock()
    ds.catalog.query = AsyncMock(return_value=CATALOG)
    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b"weak match data")
    monkeypatch.setattr(ask_mod, "rank_assets", lambda q, cats: _ranked_with_score(0.5))

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "loosely related", providers=[{"dsp": "d", "id": "p"}], top_k=1, min_score=0.0, raw=True
    )

    assert result == b"weak match data"
    ds.negotiate.assert_called_once()
