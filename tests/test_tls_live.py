"""End-to-end TLS tests: a real HTTPS handshake against an in-process server.

test_tls_wiring.py only asserts the verify/cert kwargs reach httpx. These tests
go further: they stand up a self-signed HTTPS server (via trustme) and verify the
actual TLS behavior the remote-connector feature promises —

  - a CA bundle trusts a server cert signed by that CA,
  - verify=True (system trust) rejects a self-signed cert,
  - verify=False connects anyway,
  - mutual TLS works with a client cert and is rejected without one,
  - transfer.fetch_data (the data-plane fetch — the original bug) honors TLS.
"""

from __future__ import annotations

import http.server
import ssl
import threading

import httpx
import pytest

trustme = pytest.importorskip("trustme")

from pythia._http import EDCClient  # noqa: E402
from pythia.config import TLSConfig  # noqa: E402
from pythia.models import EDRToken  # noqa: E402
from pythia.transfer import TransferController  # noqa: E402

BODY = b'{"ok": true, "value": 42}'


class _Handler(http.server.BaseHTTPRequestHandler):
    def _respond(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def log_message(self, *args: object) -> None:
        pass


def _serve(ssl_ctx: ssl.SSLContext):
    """Start an HTTPS server on an ephemeral port; return (httpd, base_url)."""
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"https://localhost:{httpd.server_address[1]}"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ca() -> trustme.CA:
    return trustme.CA()


@pytest.fixture(scope="module")
def ca_bundle(ca, tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("tls") / "ca.pem"
    ca.cert_pem.write_to_path(str(path))
    return str(path)


@pytest.fixture(scope="module")
def client_cert(ca, tmp_path_factory) -> tuple[str, str]:
    leaf = ca.issue_cert("client@pythia.test")
    d = tmp_path_factory.mktemp("clientcert")
    cert_path, key_path = d / "client.pem", d / "client.key"
    leaf.cert_chain_pems[0].write_to_path(str(cert_path))
    leaf.private_key_pem.write_to_path(str(key_path))
    return str(cert_path), str(key_path)


@pytest.fixture
def https_server(ca):
    """One-way TLS server (server cert only)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("localhost", "127.0.0.1").configure_cert(ctx)
    httpd, url = _serve(ctx)
    yield url
    httpd.shutdown()


@pytest.fixture
def mtls_server(ca):
    """Mutual TLS server — requires a client cert signed by our CA."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("localhost", "127.0.0.1").configure_cert(ctx)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ca.configure_trust(ctx)
    httpd, url = _serve(ctx)
    yield url
    httpd.shutdown()


# ── one-way TLS ─────────────────────────────────────────────────────────────


async def test_ca_bundle_trusts_good_cert(https_server, ca_bundle):
    client = EDCClient(management_url=https_server, tls=TLSConfig(ca_bundle=ca_bundle))
    try:
        data = await client.get("/anything")
    finally:
        await client.aclose()
    assert data["value"] == 42


async def test_default_verify_rejects_self_signed(https_server):
    # verify=True against system trust store — the self-signed cert must be rejected.
    client = EDCClient(management_url=https_server, tls=TLSConfig())
    with pytest.raises(httpx.TransportError):
        await client.get("/anything")
    await client.aclose()


async def test_verify_false_connects_to_self_signed(https_server):
    client = EDCClient(management_url=https_server, tls=TLSConfig(verify=False))
    try:
        data = await client.get("/anything")
    finally:
        await client.aclose()
    assert data["ok"] is True


async def test_transfer_fetch_data_over_https(https_server, ca_bundle):
    # The original bug: the data-plane fetch ignored TLS. Prove it handshakes now.
    ctrl = TransferController(client=object(), tls=TLSConfig(ca_bundle=ca_bundle))
    edr = EDRToken(endpoint=f"{https_server}/data", authorization="tok")
    out = await ctrl.fetch_data(edr)
    assert out == BODY


# ── mutual TLS ───────────────────────────────────────────────────────────────


async def test_mtls_with_client_cert_succeeds(mtls_server, ca_bundle, client_cert):
    cert, key = client_cert
    client = EDCClient(
        management_url=mtls_server,
        tls=TLSConfig(ca_bundle=ca_bundle, client_cert=cert, client_key=key),
    )
    try:
        data = await client.get("/anything")
    finally:
        await client.aclose()
    assert data["value"] == 42


async def test_mtls_without_client_cert_rejected(mtls_server, ca_bundle):
    client = EDCClient(management_url=mtls_server, tls=TLSConfig(ca_bundle=ca_bundle))
    with pytest.raises(httpx.TransportError):
        await client.get("/anything")
    await client.aclose()
