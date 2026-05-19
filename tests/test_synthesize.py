"""
Tests for the Synthesizer slice: fetching top-k assets and synthesizing a tabular answer.

The LLM is a renderer over provided data; it never invents rows or numbers.
Live tests against a local model skip if Ollama is unreachable.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pythia.llm import OllamaClient
from pythia.synthesize import Answer, FetchedAsset, LLMSynthesizer

OLLAMA_URL = "http://localhost:11434"
LIVE_MODEL = "gemma4:e4b"


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


# ── Answer.to_markdown ────────────────────────────────────────────────────────


def test_to_markdown_non_empty_table():
    answer = Answer(
        query="CO2 by maker",
        table=[
            {"maker": "BMW", "co2_tonnes": 1890},
            {"maker": "VW", "co2_tonnes": 2340},
        ],
        sources=[
            {"asset_id": "a1", "provider_id": "p1", "title": "BMW CO2 data"},
        ],
    )
    md = answer.to_markdown()
    assert "| maker |" in md
    assert "BMW" in md
    assert "VW" in md
    assert "1890" in md
    assert "Sources" in md
    assert "a1" in md


def test_to_markdown_empty_table():
    answer = Answer(
        query="CO2 by maker",
        table=[],
        sources=[],
    )
    md = answer.to_markdown()
    assert isinstance(md, str)
    assert md  # non-empty — should explain there are no results


def test_to_markdown_provenance_footer():
    answer = Answer(
        query="test",
        table=[{"x": 1}],
        sources=[
            {"asset_id": "asset-99", "provider_id": "prov-A", "title": "Dataset A"},
        ],
    )
    md = answer.to_markdown()
    assert "asset-99" in md
    assert "prov-A" in md


# ── LLMSynthesizer with stubbed OllamaClient ──────────────────────────────────


class _StubClient:
    """OllamaClient stub — returns a fixed response without hitting the network."""

    def __init__(self, response: str) -> None:
        self._response = response

    async def generate(self, prompt: str, system: str | None = None) -> str:
        return self._response


@pytest.mark.asyncio
async def test_llm_synthesizer_returns_answer_from_json():
    json_response = json.dumps([
        {"maker": "BMW", "co2_tonnes": 1890},
        {"maker": "VW", "co2_tonnes": 2340},
    ])
    stub = _StubClient(json_response)

    sources = [
        FetchedAsset(
            asset_id="asset-bmw",
            provider_id="provider-1",
            title="BMW CO2 Report",
            data=b'{"maker":"BMW","co2_tonnes":1890}',
        ),
        FetchedAsset(
            asset_id="asset-vw",
            provider_id="provider-1",
            title="VW CO2 Report",
            data=b'{"maker":"VW","co2_tonnes":2340}',
        ),
    ]

    synth = LLMSynthesizer(client=stub)  # type: ignore[arg-type]
    answer = await synth.synthesize("CO2 by maker", sources)

    assert isinstance(answer, Answer)
    assert len(answer.table) == 2
    assert answer.table[0]["maker"] == "BMW"
    assert len(answer.sources) == 2
    assert answer.sources[0]["asset_id"] == "asset-bmw"
    assert answer.sources[1]["asset_id"] == "asset-vw"


@pytest.mark.asyncio
async def test_llm_synthesizer_json_in_markdown_fences():
    """Model wraps JSON in ```json ... ``` — synthesizer should strip fences."""
    rows = [{"value": 42}]
    fenced = f"```json\n{json.dumps(rows)}\n```"
    stub = _StubClient(fenced)
    sources = [FetchedAsset(asset_id="a1", provider_id="p1", title=None, data=b'{"value":42}')]

    synth = LLMSynthesizer(client=stub)  # type: ignore[arg-type]
    answer = await synth.synthesize("test", sources)

    assert answer.table == rows
    assert answer.note is None


@pytest.mark.asyncio
async def test_llm_synthesizer_model_unreachable_does_not_crash():
    """If the model call raises (e.g. Ollama down), return a noted Answer, never crash."""

    class _DeadClient:
        async def generate(self, prompt: str, system: str | None = None) -> str:
            raise ConnectionError("daemon down")

    sources = [FetchedAsset(asset_id="a1", provider_id="p1", title="T", data=b"{}")]
    answer = await LLMSynthesizer(client=_DeadClient()).synthesize("q", sources)  # type: ignore[arg-type]

    assert answer.table == []
    assert answer.note is not None and "unreachable" in answer.note.lower()
    assert answer.sources[0]["asset_id"] == "a1"


@pytest.mark.asyncio
async def test_llm_synthesizer_parse_failure_fallback():
    """Non-JSON output → Answer with a note and no crash."""
    stub = _StubClient("I cannot help with that.")

    sources = [FetchedAsset(asset_id="a1", provider_id="p1", title="T", data=b"{}")]
    synth = LLMSynthesizer(client=stub)  # type: ignore[arg-type]
    answer = await synth.synthesize("anything", sources)

    assert isinstance(answer, Answer)
    assert answer.table == []
    assert answer.note is not None
    assert "parse" in answer.note.lower() or "failed" in answer.note.lower()


# ── ask() render-path integration (synthesis on by default) ──────────────────


def _catalog_with_assets(*asset_dicts):
    from pythia.models import Catalog, CatalogAsset

    assets = [CatalogAsset.from_dcat(d) for d in asset_dicts]
    return Catalog(
        provider_dsp="http://provider:19194/protocol",
        provider_id="provider-1",
        assets=assets,
    )


@pytest.mark.asyncio
async def test_ask_synthesize_returns_answer():
    """ask() (render default) fetches multiple assets and calls the synthesizer."""
    from pythia.ask import AskController

    catalog = _catalog_with_assets(
        {
            "@id": "asset-co2",
            "title": "CO2 2023",
            "description": "CO2 emissions German automotive",
            "hasPolicy": [{"@id": "offer-co2", "target": "asset-co2"}],
        },
        {
            "@id": "asset-energy",
            "title": "Energy Q1",
            "description": "Energy usage manufacturing",
            "hasPolicy": [{"@id": "offer-energy", "target": "asset-energy"}],
        },
    )

    ds = MagicMock()
    ds.catalog = MagicMock(query=AsyncMock(return_value=catalog))
    ds.negotiate = AsyncMock(side_effect=["agr-001", "agr-002"])
    ds.fetch = AsyncMock(side_effect=[
        b'{"maker":"BMW","co2_tonnes":1890}',
        b'{"energy_kwh":5000}',
    ])

    expected_answer = Answer(
        query="CO2 emissions",
        table=[{"maker": "BMW", "co2_tonnes": 1890}],
        sources=[{"asset_id": "asset-co2", "provider_id": "provider-1", "title": "CO2 2023"}],
    )

    stub_synth = MagicMock()
    stub_synth.synthesize = AsyncMock(return_value=expected_answer)

    ctrl = AskController(ds, synthesizer=stub_synth)
    result = await ctrl.ask(
        "CO2 emissions",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider-1"}],
        top_k=3,
        min_score=0.0,  # exercise synthesis mechanics, not the relevance gate
    )

    assert isinstance(result, Answer)
    assert result is expected_answer
    stub_synth.synthesize.assert_called_once()
    call_args = stub_synth.synthesize.call_args
    fetched_sources = call_args[0][1]
    assert len(fetched_sources) >= 1
    assert any(s.asset_id == "asset-co2" for s in fetched_sources)


@pytest.mark.asyncio
async def test_ask_synthesize_provenance():
    """Answer returned from synthesizer carries provenance from fetched sources."""
    from pythia.ask import AskController

    catalog = _catalog_with_assets(
        {
            "@id": "asset-xyz",
            "title": "XYZ Data",
            "description": "Some dataset",
            "hasPolicy": [{"@id": "offer-xyz", "target": "asset-xyz"}],
        },
    )

    ds = MagicMock()
    ds.catalog = MagicMock(query=AsyncMock(return_value=catalog))
    ds.negotiate = AsyncMock(return_value="agr-xyz")
    ds.fetch = AsyncMock(return_value=b'{"x": 1}')

    stub_synth = MagicMock()

    async def _capture_synthesize(query, sources):
        return Answer(
            query=query,
            table=[{"x": 1}],
            sources=[
                {
                    "asset_id": s.asset_id,
                    "provider_id": s.provider_id,
                    "title": s.title,
                }
                for s in sources
            ],
        )

    stub_synth.synthesize = _capture_synthesize

    ctrl = AskController(ds, synthesizer=stub_synth)
    result = await ctrl.ask(
        "test query",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider-1"}],
        top_k=1,
        min_score=0.0,  # exercise provenance, not the relevance gate
    )

    assert isinstance(result, Answer)
    assert result.sources[0]["asset_id"] == "asset-xyz"
    assert result.sources[0]["provider_id"] == "provider-1"


@pytest.mark.asyncio
async def test_ask_synthesize_noted_answer_when_nothing_fetched():
    """Render path returns a noted Answer (not None) if all fetch attempts fail —
    the synthesizer is never called because there is nothing to synthesize."""
    from pythia.ask import AskController

    catalog = _catalog_with_assets(
        {
            "@id": "asset-fail",
            "title": "Failing Asset",
            "description": "This will fail",
            "hasPolicy": [{"@id": "offer-fail", "target": "asset-fail"}],
        },
    )

    ds = MagicMock()
    ds.catalog = MagicMock(query=AsyncMock(return_value=catalog))
    ds.negotiate = AsyncMock(side_effect=Exception("network error"))

    stub_synth = MagicMock()
    stub_synth.synthesize = AsyncMock(return_value=Answer(query="q", table=[], sources=[]))

    ctrl = AskController(ds, synthesizer=stub_synth)
    result = await ctrl.ask(
        "anything",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider-1"}],
        top_k=1,
        min_score=0.0,  # exercise the fetch-failure path, not the relevance gate
    )

    assert isinstance(result, Answer)
    assert result.table == []
    assert result.note
    stub_synth.synthesize.assert_not_called()


# ── raw=True returns bytes ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_raw_returns_bytes():
    """ask(..., raw=True) returns the best-matching asset's raw bytes."""
    from pythia.ask import AskController

    catalog = _catalog_with_assets(
        {
            "@id": "asset-1",
            "title": "Some data",
            "description": "Some description",
            "hasPolicy": [{"@id": "offer-1", "target": "asset-1"}],
        },
    )

    ds = MagicMock()
    ds.catalog = MagicMock(query=AsyncMock(return_value=catalog))
    ds.negotiate = AsyncMock(return_value="agr-1")
    ds.fetch = AsyncMock(return_value=b"raw bytes")

    ctrl = AskController(ds)
    result = await ctrl.ask(
        "some query",
        providers=[{"dsp": "http://provider:19194/protocol", "id": "provider-1"}],
        top_k=1,
        min_score=0.0,  # exercise the raw-bytes path, not the relevance gate
        raw=True,
    )

    assert result == b"raw bytes"


# ── Live test against a local model ───────────────────────────────────────────


@pytest.mark.skipif(not _ollama_up(), reason="Ollama not running on localhost:11434")
@pytest.mark.asyncio
async def test_llm_synthesizer_live():
    """Live test: a local model synthesizes two JSON payloads into a tabular answer."""
    sources = [
        FetchedAsset(
            asset_id="bmw-co2",
            provider_id="provider-bmw",
            title="BMW CO2 Report 2023",
            data=b'{"maker":"BMW","co2_tonnes":1890}',
        ),
        FetchedAsset(
            asset_id="vw-co2",
            provider_id="provider-vw",
            title="VW CO2 Report 2023",
            data=b'{"maker":"VW","co2_tonnes":2340}',
        ),
    ]

    client = OllamaClient(model=LIVE_MODEL, timeout=120.0)
    synth = LLMSynthesizer(client=client)
    answer = await synth.synthesize("CO2 emissions by car maker", sources)

    assert isinstance(answer, Answer)
    assert len(answer.table) > 0, f"Expected non-empty table, note={answer.note!r}"

    table_text = str(answer.table)
    assert "BMW" in table_text or "bmw" in table_text.lower(), (
        f"Expected BMW in table: {answer.table}"
    )

    print(f"\n[LLMSynthesizer/{LIVE_MODEL}] answer.to_markdown():\n{answer.to_markdown()}")
