"""Tests for the CredentialSource abstraction and StaticCredentialSource."""

from __future__ import annotations

import pytest

from pythia.credential_source import CredentialSource, StaticCredentialSource

_VC = {
    "@context": ["https://www.w3.org/2018/credentials/v1"],
    "id": "https://example.org/credentials/1",
    "type": ["VerifiableCredential"],
    "issuer": "did:web:registry.gaia-x.eu",
}


async def test_static_source_returns_mapped_vc():
    src = StaticCredentialSource({"provider-1": _VC})
    result = await src.resolve({"dsp": "https://dsp.example", "id": "provider-1"})
    assert result == _VC


async def test_static_source_returns_none_for_unknown_provider():
    src = StaticCredentialSource({"provider-1": _VC})
    result = await src.resolve({"dsp": "https://dsp.example", "id": "unknown"})
    assert result is None


def test_static_source_satisfies_protocol():
    src = StaticCredentialSource({})
    assert isinstance(src, CredentialSource)


async def test_static_source_empty_map():
    src = StaticCredentialSource({})
    assert await src.resolve({"id": "anything"}) is None


async def test_static_source_provider_without_id_returns_none():
    """A provider dict lacking 'id' must return None, not raise KeyError."""
    src = StaticCredentialSource({"provider-1": _VC})
    assert await src.resolve({"dsp": "https://dsp.example"}) is None


@pytest.mark.parametrize("provider_id", ["a", "b"])
async def test_static_source_resolves_multiple(provider_id):
    vc_a = dict(_VC, id="a")
    vc_b = dict(_VC, id="b")
    src = StaticCredentialSource({"a": vc_a, "b": vc_b})
    result = await src.resolve({"id": provider_id})
    assert result["id"] == provider_id
