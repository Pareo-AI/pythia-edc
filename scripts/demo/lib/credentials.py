"""Demo-only Verifiable Credentials for the Pythia trust gate.

This module mints signed W3C Verifiable Credentials for the demo providers so
the trust gate (``verify_trust=True`` + a ``credential_source``) has something
real to verify, fully offline. It is DEMO scaffolding — not a production trust
anchor.

The story it stages:

  - A single, fixed demo certification authority (CA) issues VCs to the
    *trusted* providers (``rheinmobil``, ``zugspitze``). The CA's ``did:key``
    DID is the sole entry on the consumer's trust-list.
  - ``donautech`` presents a structurally valid, correctly self-signed VC, but
    from a SEPARATE "rogue" issuer that is NOT on the trust-list. The trust gate
    therefore rejects it with ``UntrustedIssuer`` and the query falls through to
    a trusted provider — visibly demonstrating the trust boundary.

Both issuer keys are derived from fixed seeds so the issuer DIDs (and thus the
trust-list) are stable across runs.
"""

from __future__ import annotations

import base64
import json
import os
import sys

import jcs
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

# Import the demo dataset definitions the same way sibling lib modules do.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datasets  # noqa: E402

# ── proof signing helpers (copied from tests/test_ask_credential.py) ────────────

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


def _sign_vc(vc: dict, key: ed25519.Ed25519PrivateKey) -> dict:
    """Attach a detached-JWS JsonWebSignature2020 proof, matching the verifier."""
    did = _did_key(key.public_key())
    payload = {k: v for k, v in vc.items() if k != "proof"}
    payload["issuer"] = did  # set issuer BEFORE signing so the proof covers it
    header_b64 = _b64url(
        json.dumps({"alg": "EdDSA", "b64": False, "crit": ["b64"]}).encode()
    )
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


# ── deterministic demo issuer keys (fixed seeds → stable DIDs) ──────────────────

# Demo certification authority. Trusted: its DID is the sole trust-list entry.
_CA_SEED = b"pythia-demo-certification-auth!!"  # 32 bytes
# A separate "rogue" / unrecognized issuer. Self-consistent, but NOT trusted.
_ROGUE_SEED = b"pythia-demo-rogue-issuer-key-x!!"  # 32 bytes

assert len(_CA_SEED) == 32 and len(_ROGUE_SEED) == 32

_CA_KEY = ed25519.Ed25519PrivateKey.from_private_bytes(_CA_SEED)
_ROGUE_KEY = ed25519.Ed25519PrivateKey.from_private_bytes(_ROGUE_SEED)

# Providers whose VCs are signed by the rogue (untrusted) issuer.
_UNTRUSTED_PROVIDERS = frozenset({"donautech"})


def _vc_for(provider: datasets.Provider) -> dict:
    """Build and sign a LegalParticipant VC for one demo provider.

    Trusted providers are signed by the CA key; the untrusted provider(s) are
    signed by the rogue key (valid structure + self-consistent proof, but an
    issuer DID that is not on the consumer's trust-list).
    """
    vc = {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://w3id.org/security/suites/jws-2020/v1",
        ],
        "id": f"https://example.org/credentials/{provider.id}",
        "type": ["VerifiableCredential", "LegalParticipantCredential"],
        "issuanceDate": "2024-01-01T00:00:00Z",
        "expirationDate": "2100-01-01T00:00:00Z",
        "credentialSubject": {
            "id": f"did:web:{provider.id}.example",
            "type": "gx:LegalParticipant",
            "gx:legalName": provider.name,
        },
    }
    key = _ROGUE_KEY if provider.id in _UNTRUSTED_PROVIDERS else _CA_KEY
    return _sign_vc(vc, key)


def credential_map() -> dict[str, dict]:
    """``{provider.id: signed_vc}`` for every demo provider."""
    return {p.id: _vc_for(p) for p in datasets.PROVIDERS}


def trust_list() -> set[str]:
    """The consumer's trust anchor: just the demo CA's did:key DID.

    The rogue issuer used for ``donautech`` is deliberately excluded, so its VC
    is rejected as ``UntrustedIssuer``.
    """
    return {_did_key(_CA_KEY.public_key())}
