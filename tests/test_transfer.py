"""
Unit tests for TransferController using AsyncMock.

Tests cover:
- Happy path: POST → STARTED → EDR retrieved
- TERMINATED raises TransferError
- Timeout raises TransferTimeout
- EDR with namespace-prefixed keys
- EDR missing endpoint raises EDRError
- fetch_data uses correct Authorization header
- fetch_data streams and enforces the response-size cap
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from pythia._http import EDCClient
from pythia.errors import EDRError, TransferError, TransferTimeout
from pythia.transfer import TransferController


@pytest.fixture
def mock_client():
    return AsyncMock(spec=EDCClient)


@pytest.mark.asyncio
async def test_happy_path(mock_client):
    """POST → STARTED in one poll → EDR retrieved."""
    mock_client.post.return_value = {"@id": "txfr-001"}
    mock_client.get.side_effect = [
        # Poll: state STARTED
        {"@id": "txfr-001", "state": "STARTED"},
        # EDR fetch
        {
            "endpoint": "http://provider:19291/public",
            "authorization": "eyJhbGci.test.token",
        },
    ]

    ctrl = TransferController(mock_client)
    transfer_id = await ctrl.start(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
        contract_id="agr-001",
        asset_id="co2-2023",
        poll_interval=0.01,
    )
    assert transfer_id == "txfr-001"

    edr = await ctrl.edr(transfer_id)
    assert edr.endpoint == "http://provider:19291/public"
    assert edr.headers == {"Authorization": "eyJhbGci.test.token"}


@pytest.mark.asyncio
async def test_terminated_raises_transfer_error(mock_client):
    """TERMINATED state raises TransferError with transfer_id and state."""
    mock_client.post.return_value = {"@id": "txfr-002"}
    mock_client.get.return_value = {"@id": "txfr-002", "state": "TERMINATED"}

    ctrl = TransferController(mock_client)
    with pytest.raises(TransferError) as exc_info:
        await ctrl.start(
            provider_dsp="http://provider:19194/protocol",
            provider_id="provider",
            contract_id="agr-002",
            asset_id="co2-2023",
            poll_interval=0.01,
        )
    assert exc_info.value.transfer_id == "txfr-002"
    assert exc_info.value.state == "TERMINATED"


@pytest.mark.asyncio
async def test_timeout_raises_transfer_timeout(mock_client):
    """Transfer never reaches STARTED → TransferTimeout."""
    mock_client.post.return_value = {"@id": "txfr-003"}
    mock_client.get.return_value = {"@id": "txfr-003", "state": "PROVISIONING"}

    ctrl = TransferController(mock_client)
    with pytest.raises(TransferTimeout):
        await ctrl.start(
            provider_dsp="http://provider:19194/protocol",
            provider_id="provider",
            contract_id="agr-003",
            asset_id="co2-2023",
            timeout=0.05,
            poll_interval=0.02,
        )


@pytest.mark.asyncio
async def test_no_id_in_response_raises_transfer_error(mock_client):
    """Missing @id in POST response raises TransferError."""
    mock_client.post.return_value = {"error": "bad"}

    ctrl = TransferController(mock_client)
    with pytest.raises(TransferError, match="No @id"):
        await ctrl.start(
            provider_dsp="http://provider:19194/protocol",
            provider_id="provider",
            contract_id="agr-004",
            asset_id="co2-2023",
        )


@pytest.mark.asyncio
async def test_edr_namespace_keys(mock_client):
    """EDR with namespace-prefixed keys is parsed correctly."""
    mock_client.get.return_value = {
        "https://w3id.org/edc/v0.0.1/ns/endpoint": "http://provider:19291/public",
        "https://w3id.org/edc/v0.0.1/ns/authorization": "bearer-token-xyz",
    }

    ctrl = TransferController(mock_client)
    edr = await ctrl.edr("txfr-005")
    assert edr.endpoint == "http://provider:19291/public"
    assert edr.authorization == "bearer-token-xyz"


@pytest.mark.asyncio
async def test_edr_missing_endpoint_raises_edr_error(mock_client):
    """EDR missing endpoint field raises EDRError."""
    mock_client.get.return_value = {
        "authorization": "some-token",
        # no endpoint
    }

    ctrl = TransferController(mock_client)
    with pytest.raises(EDRError, match="endpoint"):
        await ctrl.edr("txfr-006")


@pytest.mark.asyncio
async def test_request_body_structure(mock_client):
    """POST body includes required transfer fields."""
    from pythia.models import PROTOCOL
    mock_client.post.return_value = {"@id": "txfr-007"}
    mock_client.get.return_value = {"@id": "txfr-007", "state": "STARTED"}

    ctrl = TransferController(mock_client)
    await ctrl.start(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
        contract_id="agr-007",
        asset_id="asset-007",
        poll_interval=0.01,
    )

    path, body = mock_client.post.call_args[0]
    assert path == "/v3/transferprocesses"
    assert body["protocol"] == PROTOCOL
    assert body["contractId"] == "agr-007"
    assert body["assetId"] == "asset-007"
    assert body["dataDestination"] == {"type": "HttpProxy"}
    assert body["transferType"] == "HttpData-PULL"


# ── fetch_data: streaming, auth header, and the response-size cap ─────────────


class _FakeStream:
    """Minimal async-streaming httpx.Response stand-in for fetch_data tests."""

    def __init__(self, chunks: list[bytes], headers: dict | None = None) -> None:
        self._chunks = chunks
        self.headers = headers if headers is not None else {}
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeStreamClient:
    """Stands in for httpx.AsyncClient; records the last stream() call."""

    last_call: tuple | None = None

    def __init__(self, chunks: list[bytes], headers: dict | None = None) -> None:
        self._chunks = chunks
        self._headers = headers

    def __call__(self, *args: object, **kwargs: object) -> _FakeStreamClient:
        return self  # construction: httpx.AsyncClient(...) → self

    async def __aenter__(self) -> _FakeStreamClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def stream(self, method: str, url: str, headers: dict | None = None) -> _FakeStream:
        type(self).last_call = (method, url, headers)
        return _FakeStream(self._chunks, self._headers)


@pytest.mark.asyncio
async def test_fetch_data_uses_auth_header():
    """fetch_data streams and sends the Authorization header from the EDR token."""
    from pythia.models import EDRToken

    edr = EDRToken(endpoint="http://provider:19291/public", authorization="eyJhbGci.test")
    ctrl = TransferController(AsyncMock(spec=EDCClient))
    fake = _FakeStreamClient([b'{"data', b'":1}'], headers={"Content-Length": "10"})

    with patch("httpx.AsyncClient", fake):
        data = await ctrl.fetch_data(edr)

    assert data == b'{"data":1}'
    assert _FakeStreamClient.last_call == (
        "GET",
        "http://provider:19291/public",
        {"Authorization": "eyJhbGci.test"},
    )


@pytest.mark.asyncio
async def test_fetch_data_aborts_on_streamed_overflow():
    """A body streaming past the cap raises EDRError (no declared Content-Length)."""
    from pythia.models import EDRToken

    edr = EDRToken(endpoint="http://provider/data", authorization="tok")
    ctrl = TransferController(AsyncMock(spec=EDCClient), max_response_bytes=8)
    # Three 4-byte chunks = 12 bytes > 8-byte cap; no Content-Length header.
    fake = _FakeStreamClient([b"aaaa", b"bbbb", b"cccc"], headers={})

    with patch("httpx.AsyncClient", fake):
        with pytest.raises(EDRError, match="exceeded the 8-byte cap"):
            await ctrl.fetch_data(edr)


@pytest.mark.asyncio
async def test_fetch_data_fails_fast_on_declared_length():
    """A Content-Length over the cap is rejected before any body is read."""
    from pythia.models import EDRToken

    edr = EDRToken(endpoint="http://provider/data", authorization="tok")
    ctrl = TransferController(AsyncMock(spec=EDCClient), max_response_bytes=8)
    fake = _FakeStreamClient([b"x"], headers={"Content-Length": "1000000"})

    with patch("httpx.AsyncClient", fake):
        with pytest.raises(EDRError, match="declares 1000000 bytes"):
            await ctrl.fetch_data(edr)


@pytest.mark.asyncio
async def test_fetch_data_per_call_cap_override():
    """max_bytes on the call overrides the controller default."""
    from pythia.models import EDRToken

    edr = EDRToken(endpoint="http://provider/data", authorization="tok")
    ctrl = TransferController(AsyncMock(spec=EDCClient), max_response_bytes=1024)
    fake = _FakeStreamClient([b"aaaa", b"bbbb"], headers={})

    with patch("httpx.AsyncClient", fake):
        with pytest.raises(EDRError, match="exceeded the 4-byte cap"):
            await ctrl.fetch_data(edr, max_bytes=4)
