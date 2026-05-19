"""
Tests for the Explainer slice: rendering structured trust rejections into prose.

The verdict is decided by SHACL (trust.py); the Explainer only rephrases the
structured TrustFailures. The LLMExplainer test runs against a local Ollama and
skips if the daemon is unreachable.
"""

import httpx
import pytest

from pythia.errors import TrustError, TrustFailure
from pythia.explain import LLMExplainer, TemplateExplainer
from pythia.llm import OllamaClient
from pythia.trust import validate_offer

OLLAMA_URL = "http://localhost:11434"
TEST_MODEL = "gemma4:e4b"

BAD_OFFER_MISSING_TARGET = {
    "@id": "offer:abc:malformed",
    "assigner": "provider",
}


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


# ── trust.py now yields structured failures ────────────────────────────────────


def test_trust_error_carries_structured_failures():
    """validate_offer raises with structured TrustFailures, not just a string."""
    with pytest.raises(TrustError) as exc:
        validate_offer(BAD_OFFER_MISSING_TARGET)

    failures = exc.value.failures
    assert failures, "expected at least one structured failure"
    assert all(isinstance(f, TrustFailure) for f in failures)
    # The missing target is a MinCount violation on the target path.
    assert any(
        f.constraint == "MinCountConstraintComponent" and (f.result_path or "").endswith("target")
        for f in failures
    ), [(_f.constraint, _f.result_path) for _f in failures]


# ── TemplateExplainer: deterministic, offline ──────────────────────────────────


@pytest.mark.asyncio
async def test_template_explainer_renders_missing_field():
    failures = [
        TrustFailure(
            constraint="MinCountConstraintComponent",
            result_path="target",
            message="Less than 1 values on ...",
        )
    ]
    text = await TemplateExplainer().explain(failures, context="offer 'x' from provider")
    assert "missing required field 'target'" in text
    assert text.startswith("Rejected offer 'x' from provider:")


@pytest.mark.asyncio
async def test_template_explainer_empty():
    text = await TemplateExplainer().explain([])
    assert "no detail" in text


# ── TemplateExplainer: VC (credential) constraints ─────────────────────────────


@pytest.mark.parametrize(
    "constraint,phrase",
    [
        ("MissingCredential", "the provider presented no verifiable credential"),
        ("MissingId", "the credential is missing an identifier"),
        ("MissingContext", "the credential is missing its @context"),
        ("MissingType", "the credential is missing a type"),
        ("MissingIssuer", "the credential does not name an issuer"),
        ("Expired", "the credential has expired"),
        ("NotYetValid", "the credential is not yet valid"),
        ("MissingProof", "the credential is not cryptographically signed"),
        ("InvalidSignature", "the credential's signature is invalid"),
        ("UnsupportedKey", "the credential uses an unsupported key type"),
        ("KeyIssuerMismatch", "the signing key is not controlled by the credential issuer"),
        ("UntrustedIssuer", "the credential issuer is not trusted"),
    ],
)
@pytest.mark.asyncio
async def test_template_explainer_renders_vc_constraints(constraint, phrase):
    failures = [TrustFailure(constraint=constraint)]
    text = await TemplateExplainer().explain(failures, context="provider VC")
    assert phrase in text
    assert text.startswith("Rejected provider VC:")


@pytest.mark.asyncio
async def test_template_explainer_renders_invalid_date_with_value():
    failures = [TrustFailure(constraint="InvalidDate", value="not-a-date")]
    text = await TemplateExplainer().explain(failures)
    assert "credential has an unparseable date" in text
    assert "not-a-date" in text


# ── End-to-end: SHACL failure → template prose ─────────────────────────────────


@pytest.mark.asyncio
async def test_real_failure_renders_via_template():
    with pytest.raises(TrustError) as exc:
        validate_offer(BAD_OFFER_MISSING_TARGET)
    text = await TemplateExplainer().explain(exc.value.failures, context="the CO2 offer")
    assert "target" in text


# ── LLMExplainer: live local model (skips if Ollama down) ──────────────────────


@pytest.mark.skipif(not _ollama_up(), reason="Ollama not running on localhost:11434")
@pytest.mark.asyncio
async def test_llm_explainer_live():
    with pytest.raises(TrustError) as exc:
        validate_offer(BAD_OFFER_MISSING_TARGET)

    explainer = LLMExplainer(OllamaClient(model=TEST_MODEL))
    text = await explainer.explain(exc.value.failures, context="a CO2 emissions offer")

    assert isinstance(text, str) and text.strip()
    print(f"\n[LLMExplainer/{TEST_MODEL}] {text}")


@pytest.mark.asyncio
async def test_llm_explainer_falls_back_when_daemon_down():
    """If the model call fails, fall back to the deterministic template — never block."""
    explainer = LLMExplainer(OllamaClient(base_url="http://localhost:1", model=TEST_MODEL))
    failures = [TrustFailure(constraint="MinCountConstraintComponent", result_path="target")]
    text = await explainer.explain(failures, context="x")
    assert "missing required field 'target'" in text


# ── ask() integration: rejection reason is rendered ────────────────────────────


@pytest.mark.asyncio
async def test_ask_renders_rejection_reason(capsys):
    from unittest.mock import AsyncMock, MagicMock

    from pythia.ask import AskController
    from pythia.models import Catalog, CatalogAsset

    bad_raw = {"@id": "offer:malformed", "assigner": "provider"}
    bad_asset = CatalogAsset.from_dcat(
        {
            "@id": "asset-only",
            "title": "CO2 Emissions 2023",
            "description": "Annual CO2 data for German automotive suppliers",
            "hasPolicy": [bad_raw],
        }
    )
    bad_asset.offers[0].raw.update(bad_raw)
    bad_asset.offers[0].target = None

    catalog = Catalog(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider",
        assets=[bad_asset],
    )

    ds = MagicMock()
    ds.catalog = MagicMock(query=AsyncMock(return_value=catalog))
    ds.negotiate = AsyncMock(return_value="agr-001")
    ds.fetch = AsyncMock(return_value=b"data")

    ctrl = AskController(ds, explainer=TemplateExplainer())
    result = await ctrl.ask(
        "CO2 emissions German automotive",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider"}],
        top_k=1,
        verify_trust=True,
        raw=True,
    )

    assert result is None
    ds.negotiate.assert_not_called()
    out = capsys.readouterr().out
    assert "rejected:" in out
    assert "target" in out
