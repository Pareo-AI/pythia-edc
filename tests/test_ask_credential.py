"""
Integration tests for provider-VC verification in the ask() trust gate.

When verify_trust=True and a credential_source is configured, AskController
resolves and verifies the PROVIDER's Verifiable Credential (provider-level
trust) in addition to validating the offer (offer-level). These tests mirror
the mocking style of test_trust.py and reuse a minimal Ed25519 did:key signer
(duplicated from test_credential.py — fine for tests).
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import jcs
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from pythia.ask import AskController
from pythia.credential_source import StaticCredentialSource
from pythia.models import Catalog, CatalogAsset
from pythia.synthesize import Answer

# ── proof signing helpers (minimal, mirrors test_credential.py) ─────────────────

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58btc_encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + out


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _did_key(public_key: ed25519.Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return "did:key:z" + _b58btc_encode(b"\xed\x01" + raw)


def _sign_vc(vc: dict, key: ed25519.Ed25519PrivateKey | None = None) -> dict:
    """Attach a detached-JWS JsonWebSignature2020 proof, matching the verifier."""
    key = key or ed25519.Ed25519PrivateKey.generate()
    did = _did_key(key.public_key())
    payload = {k: v for k, v in vc.items() if k != "proof"}
    payload["issuer"] = did  # set issuer BEFORE signing so the proof covers it
    header_b64 = _b64url(json.dumps({"alg": "EdDSA", "b64": False, "crit": ["b64"]}).encode())
    signing_input = header_b64.encode("ascii") + b"." + jcs.canonicalize(payload)
    signature = key.sign(signing_input)
    signed = dict(payload)
    signed["proof"] = {
        "type": "JsonWebSignature2020",
        "created": "2024-01-01T00:00:00Z",
        "proofPurpose": "assertionMethod",
        "verificationMethod": did + "#key-1",
        "jws": f"{header_b64}..{_b64url(signature)}",
    }
    return signed


# ── VC fixtures ─────────────────────────────────────────────────────────────────


def _good_vc(
    key: ed25519.Ed25519PrivateKey | None = None, *, expiration: str | None = None
) -> dict:
    return _sign_vc(
        {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://w3id.org/security/suites/jws-2020/v1",
            ],
            "id": "https://example.org/credentials/3732",
            "type": ["VerifiableCredential", "LegalParticipantCredential"],
            "issuanceDate": "2024-01-01T00:00:00Z",
            "expirationDate": expiration or "2100-01-01T00:00:00Z",
            "credentialSubject": {
                "id": "did:web:participant.example.com",
                "type": "gx:LegalParticipant",
                "gx:legalName": "Example Corp",
            },
        },
        key,
    )


# ── catalog / ds helpers (mirrors test_trust.py) ────────────────────────────────


def _good_asset(asset_id: str = "asset-co2-2023") -> CatalogAsset:
    raw = {
        "@id": asset_id,
        "title": "CO2 Emissions 2023",
        "description": "Annual CO2 data for German automotive suppliers",
        "hasPolicy": [
            {
                "@id": f"offer:{asset_id}",
                "assigner": "provider",
                "target": asset_id,
            }
        ],
    }
    asset = CatalogAsset.from_dcat(raw)
    asset.offers[0].raw.update(raw["hasPolicy"][0])
    asset.offers[0].target = asset_id
    return asset


def _catalog(assets: list[CatalogAsset]) -> Catalog:
    return Catalog(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
        assets=assets,
    )


def _ds(catalog: Catalog) -> MagicMock:
    ds = MagicMock()
    mock_catalog_ctrl = MagicMock()
    mock_catalog_ctrl.query = AsyncMock(return_value=catalog)
    ds.catalog = mock_catalog_ctrl
    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b"data")
    return ds


PROVIDERS = [{"dsp": "http://provider:19194/protocol", "id": "provider"}]
QUERY = "CO2 emissions German automotive"


# ── tests ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_vc_skips_candidate():
    """A provider whose VC fails verification (expired) is skipped; no negotiate."""
    expired_vc = _good_vc(expiration="2020-01-01T00:00:00Z")
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(
        ds, credential_source=StaticCredentialSource({"provider": expired_vc})
    )
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True, raw=True)

    assert result is None
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_valid_vc_proceeds_to_negotiate():
    """A valid signed VC + a valid offer negotiates and fetches the data."""
    vc = _good_vc()
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource({"provider": vc}))
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True, raw=True)

    assert result == b"data"
    ds.negotiate.assert_called_once()
    ds.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_provider_with_no_vc_is_skipped():
    """resolve() returning None (no credential) skips the candidate."""
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource({}))
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True, raw=True)

    assert result is None
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_trust_list_excluding_issuer_skips():
    """trust_list not containing the VC issuer -> UntrustedIssuer -> skipped."""
    vc = _good_vc()
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource({"provider": vc}))
    result = await ctrl.ask(
        QUERY,
        providers=PROVIDERS,
        top_k=1,
        verify_trust=True,
        trust_list={"did:key:zSomeOtherTrustedDid"},
        raw=True,
    )

    assert result is None
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_trust_list_including_issuer_proceeds():
    """trust_list containing the VC issuer proceeds to negotiate."""
    vc = _good_vc()
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource({"provider": vc}))
    result = await ctrl.ask(
        QUERY,
        providers=PROVIDERS,
        top_k=1,
        verify_trust=True,
        trust_list={vc["issuer"]},
        raw=True,
    )

    assert result == b"data"
    ds.negotiate.assert_called_once()


@pytest.mark.asyncio
async def test_credential_source_none_back_compat():
    """verify_trust=True but no credential_source: offer-only validation as today."""
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds)  # no credential_source
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True, raw=True)

    assert result == b"data"
    ds.negotiate.assert_called_once()


@pytest.mark.asyncio
async def test_render_path_skips_provider_with_failing_vc():
    """The render path (default) also skips a provider whose VC fails verification,
    returning a noted Answer instead of negotiating."""
    expired_vc = _good_vc(expiration="2020-01-01T00:00:00Z")
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(
        ds, credential_source=StaticCredentialSource({"provider": expired_vc})
    )
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True)

    assert isinstance(result, Answer)
    assert result.table == []
    assert result.note
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_render_path_surfaces_refusal_reason_in_note():
    """The whole point of one feature: a trust refusal reaches the caller as prose
    in Answer.note (here: an expired credential), not just stdout."""
    expired_vc = _good_vc(expiration="2020-01-01T00:00:00Z")
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(
        ds, credential_source=StaticCredentialSource({"provider": expired_vc})
    )
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True)

    assert isinstance(result, Answer)
    assert result.note is not None and "expired" in result.note.lower()
    ds.negotiate.assert_not_called()


@pytest.mark.asyncio
async def test_per_provider_vc_verdict_is_cached():
    """Two ranked assets from the same provider trigger only one resolve/verify."""
    vc = _good_vc()
    catalog = _catalog([_good_asset("asset-a"), _good_asset("asset-b")])
    ds = _ds(catalog)

    source = StaticCredentialSource({"provider": vc})
    source.resolve = AsyncMock(side_effect=source.resolve)

    ctrl = AskController(ds, credential_source=source)
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=2, verify_trust=True, raw=True)

    assert result == b"data"
    assert source.resolve.await_count == 1


@pytest.mark.asyncio
async def test_now_check_used():
    """Sanity: a VC valid against the real clock proceeds (no injected now)."""
    vc = _good_vc(expiration="2100-01-01T00:00:00Z")
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(ds, credential_source=StaticCredentialSource({"provider": vc}))
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True, raw=True)

    assert result == b"data"


@pytest.mark.asyncio
async def test_now_is_real_clock():
    """Guard against accidental hard-coded now: an expired-yesterday VC fails."""
    yesterday = (datetime.now(UTC).replace(year=datetime.now(UTC).year - 1)).isoformat()
    expired_vc = _good_vc(expiration=yesterday)
    catalog = _catalog([_good_asset()])
    ds = _ds(catalog)

    ctrl = AskController(
        ds, credential_source=StaticCredentialSource({"provider": expired_vc})
    )
    result = await ctrl.ask(QUERY, providers=PROVIDERS, top_k=1, verify_trust=True, raw=True)

    assert result is None
    ds.negotiate.assert_not_called()
