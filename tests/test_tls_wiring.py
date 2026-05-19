"""Assert TLS config is threaded into the httpx clients (fast plumbing check).

This monkeypatches httpx.AsyncClient to record init kwargs — it proves the
config *reaches* the client. Real handshake behaviour (CA trust, mTLS, cert
rejection, https data-plane fetch) is covered end-to-end in test_tls_live.py.
"""

from __future__ import annotations

import httpx

from pythia._http import EDCClient
from pythia.config import TLSConfig
from pythia.models import EDRToken
from pythia.transfer import TransferController


class _FakeResponse:
    def __init__(self, content: bytes = b"payload") -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _RecordingClient:
    """Minimal stand-in for httpx.AsyncClient that records init kwargs."""

    last_kwargs: dict = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).last_kwargs = kwargs

    async def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse()

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def test_edcclient_passes_verify_false(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    EDCClient(management_url="https://edc/management", tls=TLSConfig(verify=False))
    assert _RecordingClient.last_kwargs["verify"] is False


def test_edcclient_default_verify_true(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    EDCClient(management_url="https://edc/management")
    assert _RecordingClient.last_kwargs["verify"] is True
    assert "cert" not in _RecordingClient.last_kwargs


async def test_transfer_fetch_data_threads_tls(monkeypatch):
    # Regression guard: the data-plane fetch used to ignore TLS entirely.
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    ctrl = TransferController(client=object(), tls=TLSConfig(verify=False))
    edr = EDRToken(endpoint="https://host/data", authorization="tok")
    out = await ctrl.fetch_data(edr)
    assert out == b"payload"
    assert _RecordingClient.last_kwargs["verify"] is False
