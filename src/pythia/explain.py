"""Render trust rejections into human-/agent-readable prose.

The verdict is decided elsewhere (SHACL / VC verification) and arrives as a list
of structured ``TrustFailure``s. An Explainer only rephrases those — it never
judges validity and never invents a reason. Two implementations:

* ``TemplateExplainer`` — deterministic, offline, no model. Default.
* ``LLMExplainer``      — richer prose via a local Ollama model. Opt-in.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from .errors import TrustFailure
from .llm import OllamaClient

_CONSTRAINT_TEMPLATES = {
    "MinCountConstraintComponent": "is missing required field '{path}'",
    "MaxCountConstraintComponent": "has too many values for '{path}'",
    "DatatypeConstraintComponent": "field '{path}' has the wrong datatype (got '{value}')",
    "NodeKindConstraintComponent": "field '{path}' is the wrong kind of node (got '{value}')",
    "ClassConstraintComponent": "field '{path}' is not of the required class (got '{value}')",
    "PatternConstraintComponent": "field '{path}' does not match the required pattern",
    # Verifiable-Credential verification constraints (from credential.py).
    "MissingCredential": "the provider presented no verifiable credential",
    "MissingId": "the credential is missing an identifier",
    "MissingContext": "the credential is missing its @context",
    "MissingType": "the credential is missing a type",
    "MissingIssuer": "the credential does not name an issuer",
    "Expired": "the credential has expired",
    "NotYetValid": "the credential is not yet valid",
    "InvalidDate": "the credential has an unparseable date ({value})",
    "MissingProof": "the credential is not cryptographically signed",
    "InvalidSignature": "the credential's signature is invalid",
    "UnsupportedKey": "the credential uses an unsupported key type",
    "KeyIssuerMismatch": "the signing key is not controlled by the credential issuer",
    "UntrustedIssuer": "the credential issuer is not trusted",
}


@runtime_checkable
class Explainer(Protocol):
    async def explain(self, failures: list[TrustFailure], *, context: str = "") -> str: ...


class TemplateExplainer:
    """Deterministic, offline. Maps each SHACL constraint to a fixed sentence."""

    async def explain(self, failures: list[TrustFailure], *, context: str = "") -> str:
        if not failures:
            return "Offer rejected (no detail available)."

        lines = [_render_one(f) for f in failures]
        prefix = f"Rejected {context}: " if context else "Rejected: "
        if len(lines) == 1:
            return prefix + lines[0] + "."
        return prefix + "the offer " + "; ".join(lines) + "."


class LLMExplainer:
    """Rephrases the structured failures via a local model. Renderer only."""

    _SYSTEM = (
        "You explain why a data-space contract offer was rejected. "
        "You are given the EXACT structured validation failures as JSON. "
        "Restate them in one or two plain sentences a non-engineer understands. "
        "Do not add, invent, or soften reasons. Do not decide whether the offer is valid — "
        "it has already been rejected. Only rephrase the given failures."
    )

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()
        self._fallback = TemplateExplainer()

    async def explain(self, failures: list[TrustFailure], *, context: str = "") -> str:
        if not failures:
            return await self._fallback.explain(failures, context=context)

        payload = json.dumps([f.__dict__ for f in failures], indent=2)
        prompt = (
            f"Context: {context or 'a data-space offer'}\n\n"
            f"Validation failures (JSON):\n{payload}\n\n"
            "Explain the rejection:"
        )
        try:
            return await self._client.generate(prompt, system=self._SYSTEM)
        except Exception:
            return await self._fallback.explain(failures, context=context)


def _render_one(failure: TrustFailure) -> str:
    template = _CONSTRAINT_TEMPLATES.get(failure.constraint or "")
    if template is not None:
        return template.format(
            path=failure.result_path or "(unknown)",
            value=failure.value or "(none)",
        )
    return failure.message or f"violated constraint '{failure.constraint or 'unknown'}'"
