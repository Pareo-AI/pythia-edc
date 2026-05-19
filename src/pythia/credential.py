"""VC verification slice: structural SHACL validation + validity window."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

from .errors import CredentialError, TrustFailure

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from rdflib import Graph, Node

_SHACL_NS = "http://www.w3.org/ns/shacl#"
_CRED_NS = "https://www.w3.org/2018/credentials#"

_VC_BASE_CONTEXTS = frozenset(
    {
        "https://www.w3.org/2018/credentials/v1",
        "https://www.w3.org/ns/credentials/v2",
    }
)

_CRED_CONTEXT = {
    "cred": _CRED_NS,
    "VerifiableCredential": "cred:VerifiableCredential",
    "issuer": {"@id": "cred:issuer", "@type": "@id"},
    "credentialSubject": {"@id": "cred:credentialSubject", "@type": "@id"},
}

_DEFAULT_SHAPE: str | None = None


def _default_shape_path() -> str:
    global _DEFAULT_SHAPE
    if _DEFAULT_SHAPE is None:
        pkg = resources.files("pythia") / "shapes" / "vc.ttl"
        _DEFAULT_SHAPE = str(pkg)
    return _DEFAULT_SHAPE


def verify_credential(
    credential: dict,
    *,
    now: datetime | None = None,
    trust_list: set[str] | None = None,
) -> None:
    """Verify a structurally valid, in-date, correctly signed W3C Verifiable Credential.

    Pipeline order: structure → SHACL → trust-list → validity window →
    proof+issuer-binding. The trust-list runs before the expensive crypto so an
    untrusted issuer is rejected early, but after structure so ``_issuer_id`` is
    present.

    ``trust_list`` accepts any iterable of DID strings; it is normalized to a set.
    When None, the issuer-identity gate is skipped (no trust anchor configured) —
    structure, validity, proof, and the key/issuer binding are still enforced.
    When a set, the issuer DID must be a member, else ``UntrustedIssuer``.
    """
    try:
        import pyshacl
        from rdflib import Graph
    except ImportError as exc:
        raise ImportError(
            "verify_credential() requires pyshacl and rdflib: "
            "pip install 'pythia-edc[trust]'"
        ) from exc

    now = now or datetime.now(UTC)
    trusted = set(trust_list) if trust_list is not None else None

    _precheck_structure(credential)
    _shacl_validate(credential, pyshacl, Graph)
    if trusted is not None:
        _check_issuer_trusted(credential, trusted)
    _check_validity_window(credential, now)
    _verify_proof(credential)


def _check_issuer_trusted(credential: dict, trusted: set[str]) -> None:
    """Gate on issuer identity against a configured trust-list."""
    issuer = _issuer_id(credential.get("issuer"))
    if issuer not in trusted:
        raise CredentialError(
            "Credential issuer is not in the trust-list",
            failures=[
                TrustFailure(
                    message=f"Issuer {issuer!r} is not a trusted issuer",
                    value=str(issuer),
                    constraint="UntrustedIssuer",
                )
            ],
        )


def _precheck_structure(credential: dict) -> None:
    """Python-side checks awkward in SHACL: @id, @context base, type, issuer."""
    failures: list[TrustFailure] = []

    if not _get(credential, "id", "@id"):
        failures.append(
            TrustFailure(
                message="Verifiable Credential is missing @id (must be a named IRI)",
                constraint="MissingId",
            )
        )

    ctx = credential.get("@context")
    ctx_values = [ctx] if isinstance(ctx, str) else list(ctx) if isinstance(ctx, list) else []
    if not any(isinstance(c, str) and c in _VC_BASE_CONTEXTS for c in ctx_values):
        failures.append(
            TrustFailure(
                message=(
                    "Verifiable Credential @context must include the VC base context "
                    "(https://www.w3.org/2018/credentials/v1 or "
                    "https://www.w3.org/ns/credentials/v2)"
                ),
                constraint="MissingContext",
            )
        )

    types = _as_list(_get(credential, "type", "@type"))
    if "VerifiableCredential" not in types:
        failures.append(
            TrustFailure(
                message='type/@type must include "VerifiableCredential"',
                constraint="MissingType",
            )
        )

    issuer = credential.get("issuer")
    if not _issuer_id(issuer):
        failures.append(
            TrustFailure(
                message=(
                    "Verifiable Credential must have a non-empty issuer "
                    "(DID/URI or object with id)"
                ),
                constraint="MissingIssuer",
            )
        )

    if failures:
        raise CredentialError(
            "Credential failed structural validation", failures=failures
        )


def _shacl_validate(credential: dict, pyshacl, graph_cls) -> None:
    doc = dict(credential)

    # Validate against a self-contained local context only. The shape checks
    # cred:issuer / cred:credentialSubject, both supplied by _CRED_CONTEXT, so we
    # drop remote string @contexts rather than dereference them over the network
    # (which is slow and brittle under rate-limiting). Inline dict contexts are
    # kept since they carry no network dependency.
    existing_ctx = credential.get("@context")
    inline_ctxs = [c for c in _as_list(existing_ctx) if isinstance(c, dict)]
    doc["@context"] = [*inline_ctxs, _CRED_CONTEXT]

    doc["@type"] = "VerifiableCredential"

    data_graph = graph_cls()
    data_graph.parse(data=json.dumps(doc), format="json-ld")

    shape_graph = graph_cls()
    shape_graph.parse(_default_shape_path(), format="turtle")

    conforms, results_graph, results_text = pyshacl.validate(
        data_graph,
        shacl_graph=shape_graph,
        inference="none",
        abort_on_first=False,
        allow_infos=False,
        allow_warnings=False,
    )

    if not conforms:
        failures = _parse_results(results_graph)
        raise CredentialError(
            f"Credential failed SHACL validation: {results_text.strip()}",
            failures=failures,
        )


def _check_validity_window(credential: dict, now: datetime) -> None:
    failures: list[TrustFailure] = []

    not_before_raw = _get(credential, "issuanceDate", "validFrom")
    not_after_raw = _get(credential, "expirationDate", "validUntil")

    not_before = _parse_iso(not_before_raw, "issuanceDate/validFrom", failures)
    not_after = _parse_iso(not_after_raw, "expirationDate/validUntil", failures)

    if not_before is not None and not_before > now:
        failures.append(
            TrustFailure(
                message=f"Credential is not yet valid (valid from {not_before_raw})",
                value=str(not_before_raw),
                constraint="NotYetValid",
            )
        )

    if not_after is not None and not_after < now:
        failures.append(
            TrustFailure(
                message=f"Credential has expired (valid until {not_after_raw})",
                value=str(not_after_raw),
                constraint="Expired",
            )
        )

    if failures:
        raise CredentialError(
            "Credential is outside its validity window", failures=failures
        )


def _verify_proof(credential: dict) -> None:
    """Verify the credential's JsonWebSignature2020 detached JWS (Ed25519 did:key).

    Final pipeline step: a structurally valid, in-date VC must also carry a proof
    whose signature verifies against the public key in its verificationMethod.
    """
    try:
        import base64

        import jcs  # type: ignore[import-untyped]
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise ImportError(
            "_verify_proof() requires cryptography and jcs: "
            "pip install 'pythia-edc[trust]'"
        ) from exc

    proof = credential.get("proof")
    if isinstance(proof, list):
        # Simplification: this slice verifies only the first proof.
        proof = proof[0] if proof else None
    if not isinstance(proof, dict):
        raise CredentialError(
            "Credential has no verifiable proof",
            failures=[
                TrustFailure(
                    message="Verifiable Credential is missing a cryptographic proof",
                    constraint="MissingProof",
                )
            ],
        )

    method = proof.get("verificationMethod") or credential.get("issuer")
    method = _issuer_id(method) if isinstance(method, dict) else method

    # SECURITY: bind the verifying key to the issuer. The trust decision keys off
    # `issuer`, so the proof's verifying key MUST be controlled by that issuer —
    # else an attacker could name a TRUSTED issuer while signing with their own
    # did:key. We require the verificationMethod DID (sans #fragment) == issuer DID.
    # Scope limitation: since the key must equal the issuer and we only resolve
    # did:key offline, trusted issuers are effectively did:key DIDs; resolving
    # did:web issuer DID-documents to their keys is roadmap (full GXDCH/Notary).
    issuer_id = _issuer_id(credential.get("issuer"))
    issuer_did = issuer_id.split("#", 1)[0] if isinstance(issuer_id, str) else None
    method_did = method.split("#", 1)[0] if isinstance(method, str) else None
    if method_did != issuer_did:
        raise CredentialError(
            "Credential proof key is not controlled by the issuer",
            failures=[
                TrustFailure(
                    message=(
                        "proof.verificationMethod DID does not match the credential "
                        "issuer; the verifying key must be controlled by the issuer"
                    ),
                    value=str(method),
                    constraint="KeyIssuerMismatch",
                )
            ],
        )

    public_key = _resolve_ed25519_did_key(method, ed25519)

    jws = proof.get("jws")
    if not isinstance(jws, str) or jws.count(".") != 2:
        raise CredentialError(
            "Credential proof has no valid detached JWS",
            failures=[
                TrustFailure(
                    message="proof.jws must be a detached JWS of the form <header>..<signature>",
                    value=str(jws),
                    constraint="InvalidSignature",
                )
            ],
        )
    header_b64, _payload_b64, signature_b64 = jws.split(".")

    try:
        header = json.loads(_b64url_decode(base64, header_b64))
    except (ValueError, json.JSONDecodeError):
        raise CredentialError(
            "Credential proof has an unreadable JWS header",
            failures=[
                TrustFailure(
                    message="proof.jws header is not valid base64url-encoded JSON",
                    constraint="InvalidSignature",
                )
            ],
        )
    if not isinstance(header, dict) or header.get("alg") != "EdDSA":
        raise CredentialError(
            "Credential proof uses an unsupported JWS algorithm",
            failures=[
                TrustFailure(
                    message='JWS header alg must be "EdDSA" for JsonWebSignature2020',
                    value=str(header.get("alg") if isinstance(header, dict) else header),
                    constraint="InvalidSignature",
                )
            ],
        )
    # RFC 7515 §4.1.11: any "crit" extension we don't understand must be rejected.
    # We only support the RFC 7797 "b64" unencoded-payload extension.
    crit = header.get("crit", [])
    if not isinstance(crit, list) or any(member != "b64" for member in crit):
        raise CredentialError(
            "Credential proof uses an unsupported critical JWS extension",
            failures=[
                TrustFailure(
                    message="JWS header 'crit' declares an extension that is not supported",
                    value=str(crit),
                    constraint="InvalidSignature",
                )
            ],
        )

    # Detached JWS over an unencoded payload (RFC 7797): the signing input is
    # ASCII(header_b64) + "." + <JCS-canonical bytes of the VC sans proof>.
    payload = {k: v for k, v in credential.items() if k != "proof"}
    signing_input = header_b64.encode("ascii") + b"." + jcs.canonicalize(payload)

    try:
        signature = _b64url_decode(base64, signature_b64)
        public_key.verify(signature, signing_input)
    except InvalidSignature:
        raise CredentialError(
            "Credential proof signature is invalid",
            failures=[
                TrustFailure(
                    message="JWS signature does not verify against the credential's public key",
                    constraint="InvalidSignature",
                )
            ],
        )
    except ValueError:
        # Malformed base64url in the signature segment (attacker-controlled).
        raise CredentialError(
            "Credential proof has a malformed JWS signature",
            failures=[
                TrustFailure(
                    message="proof.jws signature segment is not valid base64url",
                    value=str(signature_b64),
                    constraint="InvalidSignature",
                )
            ],
        )


def _resolve_ed25519_did_key(method: object, ed25519) -> Ed25519PublicKey:
    """Decode an Ed25519 public key from a did:key verificationMethod."""
    failure = TrustFailure(
        message="proof verificationMethod is not a supported Ed25519 did:key",
        value=str(method),
        constraint="UnsupportedKey",
    )
    if not isinstance(method, str):
        raise CredentialError("Unsupported verification key", failures=[failure])

    did = method.split("#", 1)[0]  # strip a #fragment if present
    prefix = "did:key:z"  # 'z' multibase => base58btc
    if not did.startswith(prefix):
        raise CredentialError("Unsupported verification key", failures=[failure])

    try:
        decoded = _b58btc_decode(did[len(prefix) :])
    except ValueError:
        raise CredentialError("Unsupported verification key", failures=[failure])

    # Multicodec ed25519-pub header (0xed 0x01) + 32-byte raw key.
    if len(decoded) != 34 or decoded[0] != 0xED or decoded[1] != 0x01:
        raise CredentialError("Unsupported verification key", failures=[failure])

    return ed25519.Ed25519PublicKey.from_public_bytes(decoded[2:])


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58btc_decode(text: str) -> bytes:
    """Decode a base58btc (Bitcoin alphabet) string to bytes."""
    num = 0
    for char in text:
        index = _B58_ALPHABET.find(char)
        if index == -1:
            raise ValueError(f"invalid base58 character: {char!r}")
        num = num * 58 + index
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(text) - len(text.lstrip("1"))  # leading '1's are leading zero bytes
    return b"\x00" * pad + body


def _b64url_decode(base64, segment: str) -> bytes:
    """base64url-decode a JWS segment, restoring stripped padding."""
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _parse_iso(
    raw: object, field: str, failures: list[TrustFailure]
) -> datetime | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        failures.append(
            TrustFailure(
                message=f"{field} must be an ISO-8601 string, got {type(raw).__name__}",
                value=str(raw),
                constraint="InvalidDate",
            )
        )
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        failures.append(
            TrustFailure(
                message=f"{field} is not a valid ISO-8601 timestamp: {raw!r}",
                value=raw,
                constraint="InvalidDate",
            )
        )
        return None
    if parsed.tzinfo is None:
        # W3C VC dates are xsd:dateTime and MUST carry a timezone. Refuse to
        # guess UTC for a naive/date-only value — it would skew expiry checks.
        failures.append(
            TrustFailure(
                message=f"{field} must include a timezone offset: {raw!r}",
                value=raw,
                constraint="InvalidDate",
            )
        )
        return None
    return parsed


def _get(credential: dict, *keys: str) -> object:
    for key in keys:
        if key in credential and credential[key] not in (None, ""):
            return credential[key]
    return None


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _issuer_id(issuer: object) -> str | None:
    """Extract the issuer DID, normalized (whitespace-stripped) for comparison."""
    if isinstance(issuer, str) and issuer.strip():
        return issuer.strip()
    if isinstance(issuer, dict):
        value = issuer.get("id") or issuer.get("@id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_results(results_graph: Graph) -> list[TrustFailure]:
    """Extract structured TrustFailures from a pyshacl results graph."""
    from rdflib import RDF, Namespace

    sh = Namespace(_SHACL_NS)

    def _str(graph: Graph, subject: Node, predicate: Node) -> str | None:
        value = graph.value(subject, predicate)
        if value is None:
            return None
        return _localname(str(value))

    failures: list[TrustFailure] = []
    for result in results_graph.subjects(RDF.type, sh.ValidationResult):
        failures.append(
            TrustFailure(
                message=_raw(results_graph.value(result, sh.resultMessage)),
                focus_node=_str(results_graph, result, sh.focusNode),
                result_path=_str(results_graph, result, sh.resultPath),
                value=_raw(results_graph.value(result, sh.value)),
                constraint=_str(results_graph, result, sh.sourceConstraintComponent),
                severity=_str(results_graph, result, sh.resultSeverity),
            )
        )
    return failures


def _raw(value: object) -> str | None:
    return None if value is None else str(value)


def _localname(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri
