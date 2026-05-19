"""
Unit tests for negotiation state machine using httpx mock.

Tests cover:
- Happy path: REQUESTED → FINALIZED
- TERMINATED error path
- Timeout when state never reaches FINALIZED
- Missing contract_agreement_id on FINALIZED
"""

from unittest.mock import AsyncMock

import pytest

from pythia._http import EDCClient
from pythia.errors import NegotiationError, NegotiationTimeout
from pythia.negotiate import NegotiationController


@pytest.fixture
def mock_client():
    client = AsyncMock(spec=EDCClient)
    return client


@pytest.mark.asyncio
async def test_happy_path(mock_client):
    """REQUESTED → FINALIZED in 2 polls."""
    mock_client.post.return_value = {"@id": "neg-001"}
    mock_client.get.side_effect = [
        # Poll 1: still negotiating
        {"@id": "neg-001", "state": "AGREED"},
        # Poll 2: finalized
        {"@id": "neg-001", "state": "FINALIZED", "contractAgreementId": "agr-abc"},
    ]

    ctrl = NegotiationController(mock_client)
    agreement_id = await ctrl.start(
        provider_dsp="http://provider:9194/protocol",
        provider_id="provider",
        offer_id="offer-1",
        asset_id="asset-1",
        poll_interval=0.01,
    )
    assert agreement_id == "agr-abc"


@pytest.mark.asyncio
async def test_terminated(mock_client):
    """TERMINATED raises NegotiationError with state."""
    mock_client.post.return_value = {"@id": "neg-002"}
    mock_client.get.return_value = {"@id": "neg-002", "state": "TERMINATED"}

    ctrl = NegotiationController(mock_client)
    with pytest.raises(NegotiationError) as exc_info:
        await ctrl.start(
            provider_dsp="http://provider:9194/protocol",
            provider_id="provider",
            offer_id="offer-1",
            asset_id="asset-1",
            poll_interval=0.01,
        )
    assert exc_info.value.state == "TERMINATED"
    assert exc_info.value.negotiation_id == "neg-002"


@pytest.mark.asyncio
async def test_timeout(mock_client):
    """Timeout raises NegotiationTimeout when state never reaches FINALIZED."""
    mock_client.post.return_value = {"@id": "neg-003"}
    mock_client.get.return_value = {"@id": "neg-003", "state": "AGREED"}

    ctrl = NegotiationController(mock_client)
    with pytest.raises(NegotiationTimeout):
        await ctrl.start(
            provider_dsp="http://provider:9194/protocol",
            provider_id="provider",
            offer_id="offer-1",
            asset_id="asset-1",
            timeout=0.05,
            poll_interval=0.02,
        )


@pytest.mark.asyncio
async def test_finalized_missing_agreement_id(mock_client):
    """FINALIZED without contractAgreementId raises NegotiationError."""
    mock_client.post.return_value = {"@id": "neg-004"}
    mock_client.get.return_value = {
        "@id": "neg-004",
        "state": "FINALIZED",
        # no contractAgreementId
    }

    ctrl = NegotiationController(mock_client)
    with pytest.raises(NegotiationError, match="no contractAgreementId"):
        await ctrl.start(
            provider_dsp="http://provider:9194/protocol",
            provider_id="provider",
            offer_id="offer-1",
            asset_id="asset-1",
            poll_interval=0.01,
        )


@pytest.mark.asyncio
async def test_no_id_in_response(mock_client):
    """Missing @id in POST response raises NegotiationError."""
    mock_client.post.return_value = {"error": "bad request"}

    ctrl = NegotiationController(mock_client)
    with pytest.raises(NegotiationError):
        await ctrl.start(
            provider_dsp="http://provider:9194/protocol",
            provider_id="provider",
            offer_id="offer-1",
            asset_id="asset-1",
        )
