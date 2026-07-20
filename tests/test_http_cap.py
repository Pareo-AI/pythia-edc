"""EDCClient response-size cap: the management/catalog plane is streamed and
bounded too, since catalog payloads are proxied (attacker-influenced) provider
data. Complements test_transfer.py, which covers the data-plane fetch."""

from __future__ import annotations

import pytest

from pythia._http import EDCClient
from pythia.errors import ConnectorError


class _FakeStream:
    def __init__(self, chunks: list[bytes], headers: dict, status: int = 200) -> None:
        self._chunks = chunks
        self.headers = headers
        self.status_code = status

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeInner:
    """Replaces EDCClient._client — only needs stream() and aclose()."""

    def __init__(self, chunks: list[bytes], headers: dict, status: int = 200) -> None:
        self._chunks = chunks
        self._headers = headers
        self._status = status

    def stream(self, method: str, url: str, json: dict | None = None) -> _FakeStream:
        return _FakeStream(self._chunks, self._headers, self._status)

    async def aclose(self) -> None:
        pass


def _client_with(chunks: list[bytes], headers: dict, status: int = 200) -> EDCClient:
    client = EDCClient(management_url="http://consumer/management", max_response_bytes=8)
    client._client = _FakeInner(chunks, headers, status)  # type: ignore[assignment]
    return client


@pytest.mark.asyncio
async def test_ok_response_under_cap_parses():
    client = _client_with([b'{"a"', b":1}"], {"Content-Length": "7"})  # 7 bytes < 8-byte cap
    assert await client.get("/anything") == {"a": 1}


@pytest.mark.asyncio
async def test_streamed_overflow_raises_connector_error():
    client = _client_with([b"aaaa", b"bbbb", b"cccc"], {})  # 12 > 8
    with pytest.raises(ConnectorError, match="exceeded the 8-byte cap"):
        await client.get("/anything")


@pytest.mark.asyncio
async def test_declared_length_over_cap_fails_fast():
    client = _client_with([b"x"], {"Content-Length": "1000000"})
    with pytest.raises(ConnectorError, match="over the 8-byte cap"):
        await client.post("/catalog/request", {"q": 1})
