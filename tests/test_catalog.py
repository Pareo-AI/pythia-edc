"""
Unit tests for CatalogController using AsyncMock.

Tests cover:
- Basic catalog query → assets parsed
- dcat:dataset key variant
- Single dataset (dict, not list)
- Empty dataset list
- CatalogError raised on HTTP failure
"""

from unittest.mock import AsyncMock

import pytest

from pythia._http import EDCClient
from pythia.catalog import CatalogController
from pythia.errors import CatalogError


@pytest.fixture
def mock_client():
    return AsyncMock(spec=EDCClient)


def _dcat_dataset(asset_id: str, title: str, description: str = "") -> dict:
    return {
        "@id": asset_id,
        "title": title,
        "description": description,
        "hasPolicy": [{"@id": f"offer:{asset_id}", "target": asset_id}],
    }


@pytest.mark.asyncio
async def test_basic_catalog_query(mock_client):
    """Standard response with 'dataset' key returns parsed assets."""
    mock_client.post.return_value = {
        "@context": {"@vocab": "https://w3id.org/dspace/2024/1/"},
        "@type": "dcat:Catalog",
        "dataset": [
            _dcat_dataset("co2-2023", "CO2 Emissions 2023", "Annual CO2 data"),
            _dcat_dataset("energy-q1", "Energy Q1", "Quarterly energy"),
        ],
    }

    ctrl = CatalogController(mock_client)
    catalog = await ctrl.query(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
    )

    assert catalog.provider_id == "provider"
    assert catalog.provider_dsp == "http://provider:19194/protocol"
    assert len(catalog.assets) == 2
    assert catalog.assets[0].id == "co2-2023"
    assert catalog.assets[0].title == "CO2 Emissions 2023"
    assert len(catalog.assets[0].offers) == 1


@pytest.mark.asyncio
async def test_dcat_dataset_key_variant(mock_client):
    """'dcat:dataset' key (Gaia-X MVD variant) is handled."""
    mock_client.post.return_value = {
        "dcat:dataset": [_dcat_dataset("asset-1", "Asset One")],
    }

    ctrl = CatalogController(mock_client)
    catalog = await ctrl.query(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
    )
    assert len(catalog.assets) == 1
    assert catalog.assets[0].id == "asset-1"


@pytest.mark.asyncio
async def test_single_dataset_dict(mock_client):
    """Single dataset returned as dict (not list) is wrapped."""
    mock_client.post.return_value = {
        "dataset": _dcat_dataset("single-asset", "Single"),
    }

    ctrl = CatalogController(mock_client)
    catalog = await ctrl.query(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
    )
    assert len(catalog.assets) == 1
    assert catalog.assets[0].id == "single-asset"


@pytest.mark.asyncio
async def test_empty_catalog(mock_client):
    """Empty dataset list returns catalog with zero assets."""
    mock_client.post.return_value = {"dataset": []}

    ctrl = CatalogController(mock_client)
    catalog = await ctrl.query(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
    )
    assert catalog.assets == []


@pytest.mark.asyncio
async def test_missing_dataset_key(mock_client):
    """Response with no dataset key returns catalog with zero assets."""
    mock_client.post.return_value = {
        "@type": "dcat:Catalog",
        "@id": "urn:catalog:1",
    }

    ctrl = CatalogController(mock_client)
    catalog = await ctrl.query(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
    )
    assert catalog.assets == []


@pytest.mark.asyncio
async def test_http_error_raises_catalog_error(mock_client):
    """HTTP failure from client raises CatalogError."""
    from pythia.errors import ConnectorError
    mock_client.post.side_effect = ConnectorError("Connection refused", status_code=503)

    ctrl = CatalogController(mock_client)
    with pytest.raises(CatalogError, match="provider"):
        await ctrl.query(
            provider_dsp="http://provider:19194/protocol",
            provider_id="provider",
        )


@pytest.mark.asyncio
async def test_request_uses_correct_path(mock_client):
    """POST is called on correct management path with protocol."""
    from pythia.models import PROTOCOL
    mock_client.post.return_value = {"dataset": []}

    ctrl = CatalogController(mock_client, api_version="v3")
    await ctrl.query(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
    )

    mock_client.post.assert_called_once()
    path, body = mock_client.post.call_args[0]
    assert path == "/v3/catalog/request"
    assert body["protocol"] == PROTOCOL
    assert body["counterPartyAddress"] == "http://provider:19194/protocol"
    assert body["counterPartyId"] == "provider"
