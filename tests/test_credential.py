"""
Tests for VC verification: structural validation, validity window, and
cryptographic proof (JsonWebSignature2020 detached JWS over an Ed25519 did:key).

Fixtures are realistic Gaia-X-style Verifiable Credentials, signed in-test via a
freshly generated Ed25519 keypair so the happy paths exercise the real verifier.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import jcs
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from pythia.credential import verify_credential
from pythia.errors import CredentialError, PythiaError

# ── proof signing helpers ──────────────────────────────────────────────────────

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


# ── fixtures ───────────────────────────────────────────────────────────────────

NOW = datetime(2024, 6, 1, tzinfo=UTC)


def _good_vc() -> dict:
    return _sign_vc(
        {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://w3id.org/security/suites/jws-2020/v1",
            ],
            "id": "https://example.org/credentials/3732",
            "type": ["VerifiableCredential", "LegalParticipantCredential"],
            "issuer": "did:web:registry.gaia-x.eu",
            "issuanceDate": "2024-01-01T00:00:00Z",
            "expirationDate": "2025-01-01T00:00:00Z",
            "credentialSubject": {
                "id": "did:web:participant.example.com",
                "type": "gx:LegalParticipant",
                "gx:legalName": "Example Corp",
            },
        }
    )


def _good_vc_v2() -> dict:
    return _sign_vc(
        {
            "@context": ["https://www.w3.org/ns/credentials/v2"],
            "id": "https://example.org/credentials/9999",
            "type": ["VerifiableCredential", "LegalParticipantCredential"],
            "issuer": {"id": "did:web:registry.gaia-x.eu"},
            "validFrom": "2024-01-01T00:00:00Z",
            "validUntil": "2025-01-01T00:00:00Z",
            "credentialSubject": {
                "id": "did:web:participant.example.com",
                "type": "gx:LegalParticipant",
            },
        }
    )


# ── happy path ───────────────────────────────────────────────────────────────


def test_valid_vc_passes():
    verify_credential(_good_vc(), now=NOW)


def test_valid_vc_v2_passes():
    verify_credential(_good_vc_v2(), now=NOW)


def test_credential_error_is_pythia_error():
    assert issubclass(CredentialError, PythiaError)


# ── structural failures ──────────────────────────────────────────────────────


def test_missing_context_raises():
    vc = _good_vc()
    del vc["@context"]
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "MissingContext" for f in exc.value.failures)


def test_context_without_vc_base_raises():
    vc = _good_vc()
    vc["@context"] = ["https://example.org/some-other-context"]
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "MissingContext" for f in exc.value.failures)


def test_context_as_plain_string_accepted():
    vc = _good_vc()
    vc["@context"] = "https://www.w3.org/2018/credentials/v1"
    verify_credential(_sign_vc(vc), now=NOW)


def test_missing_id_raises():
    vc = _good_vc()
    del vc["id"]
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "MissingId" for f in exc.value.failures)


def test_missing_type_raises():
    vc = _good_vc()
    del vc["type"]
    with pytest.raises(CredentialError):
        verify_credential(vc, now=NOW)


def test_type_without_verifiable_credential_raises():
    vc = _good_vc()
    vc["type"] = ["LegalParticipantCredential"]
    with pytest.raises(CredentialError):
        verify_credential(vc, now=NOW)


def test_missing_issuer_raises():
    vc = _good_vc()
    del vc["issuer"]
    with pytest.raises(CredentialError):
        verify_credential(vc, now=NOW)


def test_empty_issuer_raises():
    vc = _good_vc()
    vc["issuer"] = ""
    with pytest.raises(CredentialError):
        verify_credential(vc, now=NOW)


def test_missing_credential_subject_raises():
    vc = _good_vc()
    del vc["credentialSubject"]
    with pytest.raises(CredentialError):
        verify_credential(vc, now=NOW)


def test_empty_credential_subject_list_raises():
    vc = _good_vc()
    vc["credentialSubject"] = []
    with pytest.raises(CredentialError):
        verify_credential(vc, now=NOW)


def test_credential_subject_list_accepted():
    vc = _good_vc()
    vc["credentialSubject"] = [
        {"id": "did:web:a.example.com", "type": "gx:LegalParticipant"}
    ]
    verify_credential(_sign_vc(vc), now=NOW)


# ── validity window ──────────────────────────────────────────────────────────


def test_expired_vc_raises():
    vc = _good_vc()
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert any(f.constraint == "Expired" for f in exc.value.failures)


def test_not_yet_valid_vc_raises():
    vc = _good_vc()
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=datetime(2023, 1, 1, tzinfo=UTC))
    assert any(f.constraint == "NotYetValid" for f in exc.value.failures)


def test_v2_validuntil_expired_raises():
    vc = _good_vc_v2()
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert any(f.constraint == "Expired" for f in exc.value.failures)


def test_v2_validfrom_not_yet_valid_raises():
    vc = _good_vc_v2()
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=datetime(2023, 1, 1, tzinfo=UTC))
    assert any(f.constraint == "NotYetValid" for f in exc.value.failures)


def test_unparseable_date_raises():
    vc = _good_vc()
    vc["issuanceDate"] = "not-a-date"
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidDate" for f in exc.value.failures)


def test_injected_now_controls_verdict():
    vc = _good_vc()
    # valid window 2024-01-01 .. 2025-01-01
    verify_credential(vc, now=datetime(2024, 6, 1, tzinfo=UTC))
    with pytest.raises(CredentialError):
        verify_credential(vc, now=datetime(2025, 6, 1, tzinfo=UTC))


def test_default_now_uses_current_time():
    # A VC that expired in the past must fail with the real clock.
    vc = _good_vc()
    vc["issuanceDate"] = "2000-01-01T00:00:00Z"
    vc["expirationDate"] = "2001-01-01T00:00:00Z"
    with pytest.raises(CredentialError):
        verify_credential(vc)


def test_no_validity_dates_passes():
    vc = _good_vc()
    del vc["issuanceDate"]
    del vc["expirationDate"]
    # Re-sign AFTER deleting dates: the proof must cover the mutated document.
    verify_credential(_sign_vc(vc), now=NOW)


def test_naive_timestamp_rejected():
    """A timestamp without a timezone offset must not be silently coerced to UTC."""
    vc = _good_vc()
    vc["issuanceDate"] = "2024-01-01T00:00:00"
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidDate" for f in exc.value.failures)


def test_date_only_timestamp_rejected():
    """A date-only value lacks a timezone and must be rejected, not assumed UTC."""
    vc = _good_vc()
    vc["expirationDate"] = "2025-01-01"
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidDate" for f in exc.value.failures)


def test_validity_window_boundaries_are_inclusive():
    """now exactly at issuance or expiry instant is treated as valid."""
    vc = _good_vc()
    verify_credential(vc, now=datetime(2024, 1, 1, tzinfo=UTC))
    verify_credential(vc, now=datetime(2025, 1, 1, tzinfo=UTC))


# ── cryptographic proof ──────────────────────────────────────────────────────


def test_signed_vc_passes():
    """Round-trip: a freshly signed good VC verifies."""
    verify_credential(_good_vc(), now=NOW)


def test_missing_proof_raises():
    vc = _good_vc()
    del vc["proof"]
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "MissingProof" for f in exc.value.failures)


def test_tampered_credential_subject_raises():
    vc = _good_vc()
    vc["credentialSubject"]["gx:legalName"] = "Evil Corp"
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidSignature" for f in exc.value.failures)


def test_wrong_key_raises():
    """Signed by key A but verificationMethod points at unrelated key B.

    Issuer is set to B so the key/issuer binding passes and the failure is the
    signature mismatch itself (distinct from the KeyIssuerMismatch test).
    """
    vc = _good_vc()
    other = _did_key(ed25519.Ed25519PrivateKey.generate().public_key())
    vc["proof"]["verificationMethod"] = other + "#key-1"
    vc["issuer"] = other  # keep issuer == method DID so binding holds
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidSignature" for f in exc.value.failures)


def test_malformed_did_key_raises():
    vc = _good_vc()
    vc["proof"]["verificationMethod"] = "did:key:zNotAValidKey"
    vc["issuer"] = "did:key:zNotAValidKey"  # keep issuer == method DID
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "UnsupportedKey" for f in exc.value.failures)


def test_non_ed25519_did_key_raises():
    """A did:key with a non-ed25519 multicodec prefix is unsupported."""
    vc = _good_vc()
    # 0xec 0x01 is x25519-pub, not ed25519-pub.
    bogus = "did:key:z" + _b58btc_encode(b"\xec\x01" + b"\x00" * 32)
    vc["proof"]["verificationMethod"] = bogus
    vc["issuer"] = bogus  # keep issuer == method DID so binding holds
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "UnsupportedKey" for f in exc.value.failures)


def test_wrong_alg_raises():
    """A JWS header declaring an algorithm other than EdDSA is rejected."""
    key = ed25519.Ed25519PrivateKey.generate()
    vc = _good_vc()
    did = vc["proof"]["verificationMethod"]
    payload = {k: v for k, v in vc.items() if k != "proof"}
    header_b64 = _b64url(json.dumps({"alg": "RS256"}).encode())
    sig = key.sign(header_b64.encode("ascii") + b"." + jcs.canonicalize(payload))
    vc["proof"]["jws"] = f"{header_b64}..{_b64url(sig)}"
    vc["proof"]["verificationMethod"] = did
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidSignature" for f in exc.value.failures)


def test_proof_as_single_element_list_accepted():
    vc = _good_vc()
    vc["proof"] = [vc["proof"]]
    verify_credential(vc, now=NOW)


def test_malformed_signature_base64_raises_credential_error():
    """A signature segment that is not valid base64url must raise CredentialError,
    not leak a raw binascii.Error (attacker-controlled input)."""
    vc = _good_vc()
    header_b64 = vc["proof"]["jws"].split(".")[0]
    vc["proof"]["jws"] = f"{header_b64}..A"  # length 1 mod 4 -> invalid base64url
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidSignature" for f in exc.value.failures)


def test_unknown_crit_extension_rejected():
    """RFC 7515 §4.1.11: an unrecognized 'crit' member must cause rejection."""
    key = ed25519.Ed25519PrivateKey.generate()
    did = _did_key(key.public_key())
    base = {k: v for k, v in _good_vc().items() if k != "proof"}
    base["issuer"] = did
    header_b64 = _b64url(
        json.dumps({"alg": "EdDSA", "b64": False, "crit": ["b64", "unknownExt"]}).encode()
    )
    sig = key.sign(header_b64.encode("ascii") + b"." + jcs.canonicalize(base))
    vc = dict(base)
    vc["proof"] = {
        "type": "JsonWebSignature2020",
        "verificationMethod": did + "#key-1",
        "jws": f"{header_b64}..{_b64url(sig)}",
    }
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "InvalidSignature" for f in exc.value.failures)


# ── key/issuer binding (security: authorization-bypass fix) ──────────────────────


def _sign_with_mismatched_method(vc: dict) -> dict:
    """Sign with key B but keep a TRUSTED issuer and point verificationMethod at B.

    The signature itself is VALID for B's key, so an unbound verifier would pass
    this — proving the binding check (issuer DID != method DID) fires first.
    """
    issuer_did = "did:web:registry.gaia-x.eu"
    attacker = ed25519.Ed25519PrivateKey.generate()
    attacker_did = _did_key(attacker.public_key())
    payload = {k: v for k, v in vc.items() if k != "proof"}
    payload["issuer"] = issuer_did  # trusted-looking issuer
    header_b64 = _b64url(json.dumps({"alg": "EdDSA", "b64": False, "crit": ["b64"]}).encode())
    signing_input = header_b64.encode("ascii") + b"." + jcs.canonicalize(payload)
    signature = attacker.sign(signing_input)  # valid signature for attacker's key
    signed = dict(payload)
    signed["proof"] = {
        "type": "JsonWebSignature2020",
        "created": "2024-01-01T00:00:00Z",
        "proofPurpose": "assertionMethod",
        "verificationMethod": attacker_did + "#key-1",  # key NOT controlled by issuer
        "jws": f"{header_b64}..{_b64url(signature)}",
    }
    return signed


def test_key_issuer_mismatch_raises():
    """A valid signature whose verificationMethod DID != issuer DID is rejected."""
    vc = _sign_with_mismatched_method(_good_vc())
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW)
    assert any(f.constraint == "KeyIssuerMismatch" for f in exc.value.failures)


# ── issuer trust-list ────────────────────────────────────────────────────────


def test_trust_list_with_issuer_passes():
    vc = _good_vc()
    issuer = vc["issuer"]
    verify_credential(vc, now=NOW, trust_list={issuer})


def test_trust_list_without_issuer_raises():
    vc = _good_vc()
    with pytest.raises(CredentialError) as exc:
        verify_credential(vc, now=NOW, trust_list={"did:key:zSomeOtherTrustedDid"})
    assert any(f.constraint == "UntrustedIssuer" for f in exc.value.failures)


def test_trust_list_none_skips_gate():
    """No trust anchor configured: a self-signed did:key VC still verifies."""
    verify_credential(_good_vc(), now=NOW, trust_list=None)


def test_trust_list_accepts_object_issuer():
    """Issuer in object form {"id": did} is matched against the trust-list."""
    key = ed25519.Ed25519PrivateKey.generate()
    did = _did_key(key.public_key())
    base = {k: v for k, v in _good_vc().items() if k != "proof"}
    base["issuer"] = {"id": did}  # object-form issuer, id == signing key DID
    header_b64 = _b64url(json.dumps({"alg": "EdDSA", "b64": False, "crit": ["b64"]}).encode())
    sig = key.sign(header_b64.encode("ascii") + b"." + jcs.canonicalize(base))
    vc = dict(base)
    vc["proof"] = {
        "type": "JsonWebSignature2020",
        "verificationMethod": did + "#key-1",
        "jws": f"{header_b64}..{_b64url(sig)}",
    }
    verify_credential(vc, now=NOW, trust_list={did})
