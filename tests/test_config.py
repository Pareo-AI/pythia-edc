"""Tests for pythia.config — TLSConfig and ConnectorConfig."""

from __future__ import annotations

import ssl

import pytest

from pythia.config import ConnectorConfig, TLSConfig

trustme = pytest.importorskip("trustme")


# ── real cert material (httpx_kwargs eagerly loads CA / client cert files) ────


@pytest.fixture(scope="module")
def _ca():
    return trustme.CA()


@pytest.fixture(scope="module")
def ca_file(_ca, tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("cfg-tls") / "ca.pem"
    _ca.cert_pem.write_to_path(str(path))
    return str(path)


@pytest.fixture(scope="module")
def client_pem(_ca, tmp_path_factory) -> dict:
    leaf = _ca.issue_cert("client@pythia.test")
    d = tmp_path_factory.mktemp("cfg-cc")
    cert, key, combined = d / "c.pem", d / "k.pem", d / "combined.pem"
    leaf.cert_chain_pems[0].write_to_path(str(cert))
    leaf.private_key_pem.write_to_path(str(key))
    leaf.private_key_and_cert_chain_pem.write_to_path(str(combined))
    return {"cert": str(cert), "key": str(key), "combined": str(combined)}


# ── TLSConfig.httpx_kwargs ──────────────────────────────────────────────────


def test_tls_defaults():
    kw = TLSConfig().httpx_kwargs()
    assert kw == {"verify": True}
    assert "cert" not in kw


def test_tls_verify_false():
    assert TLSConfig(verify=False).httpx_kwargs() == {"verify": False}


def test_tls_ca_bundle_builds_verifying_context(ca_file):
    kw = TLSConfig(ca_bundle=ca_file).httpx_kwargs()
    assert isinstance(kw["verify"], ssl.SSLContext)
    assert kw["verify"].verify_mode == ssl.CERT_REQUIRED
    assert "cert" not in kw


def test_tls_client_cert_and_key_builds_context(ca_file, client_pem):
    kw = TLSConfig(
        ca_bundle=ca_file, client_cert=client_pem["cert"], client_key=client_pem["key"]
    ).httpx_kwargs()
    assert isinstance(kw["verify"], ssl.SSLContext)


def test_tls_client_cert_only_combined_pem(client_pem):
    # A single PEM holding both cert and key, no separate key file.
    kw = TLSConfig(client_cert=client_pem["combined"]).httpx_kwargs()
    assert isinstance(kw["verify"], ssl.SSLContext)


def test_tls_verify_false_with_ca_disables_verification(ca_file):
    kw = TLSConfig(verify=False, ca_bundle=ca_file).httpx_kwargs()
    assert kw["verify"].verify_mode == ssl.CERT_NONE
    assert kw["verify"].check_hostname is False


# ── TLSConfig.from_env ──────────────────────────────────────────────────────


def test_tls_from_env_defaults(monkeypatch):
    for k in ("PYTHIA_VERIFY_SSL", "PYTHIA_CA_BUNDLE", "PYTHIA_CLIENT_CERT", "PYTHIA_CLIENT_KEY"):
        monkeypatch.delenv(k, raising=False)
    tls = TLSConfig.from_env()
    assert tls == TLSConfig()


def test_tls_from_env_all_set(monkeypatch):
    monkeypatch.setenv("PYTHIA_VERIFY_SSL", "false")
    monkeypatch.setenv("PYTHIA_CA_BUNDLE", "/ca.pem")
    monkeypatch.setenv("PYTHIA_CLIENT_CERT", "/c.pem")
    monkeypatch.setenv("PYTHIA_CLIENT_KEY", "/k.pem")
    tls = TLSConfig.from_env()
    assert tls == TLSConfig(
        verify=False, ca_bundle="/ca.pem", client_cert="/c.pem", client_key="/k.pem"
    )


def test_tls_from_env_prefix(monkeypatch):
    monkeypatch.setenv("FOO_VERIFY_SSL", "no")
    monkeypatch.setenv("FOO_CA_BUNDLE", "/ca.pem")
    tls = TLSConfig.from_env(prefix="FOO_")
    assert tls.verify is False
    assert tls.ca_bundle == "/ca.pem"


def test_tls_from_env_bool_variants(monkeypatch):
    for raw, expected in [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ]:
        monkeypatch.setenv("PYTHIA_VERIFY_SSL", raw)
        assert TLSConfig.from_env().verify is expected


def test_tls_from_env_empty_uses_default(monkeypatch):
    monkeypatch.setenv("PYTHIA_VERIFY_SSL", "")
    assert TLSConfig.from_env().verify is True


# ── ConnectorConfig.from_env ────────────────────────────────────────────────


def test_connector_defaults():
    cfg = ConnectorConfig(management_url="http://x/management")
    assert cfg.api_key == "password"
    assert cfg.api_key_header == "X-Api-Key"
    assert cfg.api_version == "v3"
    assert cfg.providers == []
    assert cfg.timeout == 30.0
    assert cfg.tls == TLSConfig()


def test_connector_from_env_defaults(monkeypatch):
    for k in (
        "PYTHIA_MANAGEMENT_URL",
        "PYTHIA_API_KEY",
        "PYTHIA_API_KEY_HEADER",
        "PYTHIA_API_VERSION",
        "PYTHIA_PROVIDERS",
        "PYTHIA_TIMEOUT",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = ConnectorConfig.from_env()
    assert cfg.management_url == "http://localhost:29193/management"
    assert cfg.api_key == "password"
    assert cfg.api_key_header == "X-Api-Key"
    assert cfg.api_version == "v3"
    assert cfg.providers == []
    assert cfg.timeout == 30.0
    assert cfg.tls == TLSConfig()


def test_connector_from_env_all_set(monkeypatch):
    monkeypatch.setenv("PYTHIA_MANAGEMENT_URL", "https://edc.example/management")
    monkeypatch.setenv("PYTHIA_API_KEY", "secret")
    monkeypatch.setenv("PYTHIA_API_KEY_HEADER", "Authorization")
    monkeypatch.setenv("PYTHIA_API_VERSION", "v4")
    monkeypatch.setenv(
        "PYTHIA_PROVIDERS", '[{"dsp": "http://p/protocol", "id": "provider"}]'
    )
    monkeypatch.setenv("PYTHIA_TIMEOUT", "12.5")
    monkeypatch.setenv("PYTHIA_VERIFY_SSL", "false")
    cfg = ConnectorConfig.from_env()
    assert cfg.management_url == "https://edc.example/management"
    assert cfg.api_key == "secret"
    assert cfg.api_key_header == "Authorization"
    assert cfg.api_version == "v4"
    assert cfg.providers == [{"dsp": "http://p/protocol", "id": "provider"}]
    assert cfg.timeout == 12.5
    assert cfg.tls.verify is False
