"""Abstraction for resolving a provider's Verifiable Credential.

Mirrors the Explainer/Synthesizer protocol idiom: a ``Protocol`` defines the
surface and a default in-memory implementation ships with it. ``resolve`` is
async to match the SDK's I/O surface (the future caller, AskController.ask, is
async) and to leave room for a network-resolving impl.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialSource(Protocol):
    async def resolve(self, provider: dict) -> dict | None:
        """Return the provider's VC dict, or None if none is available.

        ``provider`` is a provider descriptor like ``{"dsp": ..., "id": ...}``.
        """
        ...


class StaticCredentialSource:
    """In-memory source backed by a ``{provider_id: vc}`` map (caller-supplied).

    A DID / self-description resolver that fetches VCs over the network is a
    future impl.
    """

    def __init__(self, credentials: dict[str, dict]) -> None:
        self._credentials = credentials

    async def resolve(self, provider: dict) -> dict | None:
        provider_id = provider.get("id")
        if provider_id is None:
            return None
        return self._credentials.get(provider_id)
