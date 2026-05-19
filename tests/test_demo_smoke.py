"""
Demo smoke tests — assert every live demo beat end-to-end.

Requires a running demo stack (`./demo up`) AND EDC_LIVE=1.

    EDC_LIVE=1 uv run --extra dev --extra ask --extra trust python -m pytest \
        tests/test_demo_smoke.py -v

Beats covered:
    1. NL retrieval     — ds.ask("CO2 ...") returns real CO2 data bytes
    2. Trust happy path — verify_trust=True still returns real data (SHACL regression guard)
    3. Trust rejection  — validate_offer rejects a malformed offer (missing @id)
    4. MCP browse_catalog  — MCP call_tool handler returns asset listing from live stack
    5. MCP ask_dataspace   — MCP call_tool handler returns data for a CO2 query
    6. Synthesized answer  — ds.ask(...) (render default) returns Answer with CO2 table
"""

from __future__ import annotations

import os

import httpx
import pytest
from conftest import (
    API_KEY,
    CONSUMER_MANAGEMENT,
    PROVIDER_DSP,
    PROVIDER_ID,
    PROVIDERS,
)

OLLAMA_URL = "http://localhost:11434"


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(
    not os.environ.get("EDC_LIVE"),
    reason="Set EDC_LIVE=1 to run demo smoke tests against live EDC stack",
)

DATA_QUERY = "CO2 emissions for German automotive suppliers 2023"
# Fan out across every local provider connector (one per logical provider).
_PROVIDERS = PROVIDERS


def _ds():
    from pythia import DataSpace

    return DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=_PROVIDERS,
    )


# ── Beat 1: NL retrieval ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_beat1_nl_retrieval():
    """ds.ask(raw=True) returns real CO2 dataset bytes for a CO2 query."""
    async with _ds() as ds:
        data = await ds.ask(DATA_QUERY, timeout=60.0, raw=True)

    assert data is not None, "ds.ask() returned None — no matching asset negotiated"
    assert len(data) > 0, "ds.ask() returned empty bytes"

    lower = data.decode("utf-8").lower()
    assert "co2" in lower or "scope1" in lower or "tonnes" in lower, (
        f"Payload does not look like CO2 emissions data. First 300 chars:\n{lower[:300]}"
    )


# ── Beat 2: Trust happy path ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_beat2_trust_happy_path():
    """verify_trust=True still returns real data — real offers must pass SHACL."""
    async with _ds() as ds:
        data = await ds.ask(DATA_QUERY, verify_trust=True, timeout=60.0, raw=True)

    assert data is not None, (
        "ds.ask(verify_trust=True) returned None — "
        "seeded offers may be failing SHACL validation (regression)"
    )
    assert len(data) > 0


# ── Beat 3: Trust rejection ───────────────────────────────────────────────────

def test_beat3_trust_rejects_bad_offer():
    """validate_offer raises TrustError for an offer missing @id."""
    from pythia.errors import TrustError
    from pythia.trust import validate_offer

    with pytest.raises(TrustError, match="missing @id"):
        validate_offer({"@type": "odrl:Offer"}, target=None)


# ── Beats 4 & 5: MCP tool surface ────────────────────────────────────────────
#
# Approach: create_server() registers a CallToolRequest handler via the
# @server.call_tool() decorator.  We retrieve it from server.request_handlers
# and call it directly with a CallToolRequest, bypassing the stdio transport.
# This exercises the identical code path the MCP server runs when an AI client
# invokes a tool, without requiring a running MCP process or stdio wiring.

async def _mcp_call(tool_name: str, arguments: dict) -> str:
    """Invoke an MCP tool via the registered handler; return concatenated text."""
    import mcp.types

    from pythia.mcp import create_server

    server = create_server(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=_PROVIDERS,
    )
    handler = server.request_handlers[mcp.types.CallToolRequest]
    req = mcp.types.CallToolRequest(
        params=mcp.types.CallToolRequestParams(name=tool_name, arguments=arguments)
    )
    result = await handler(req)
    # result is a ServerResult wrapping CallToolResult(content=[TextContent, ...])
    return "\n".join(item.text for item in result.root.content)


@pytest.mark.asyncio
async def test_beat4_mcp_browse_catalog():
    """MCP browse_catalog tool returns asset listing from live provider."""
    text = await _mcp_call(
        "browse_catalog",
        {"provider_dsp": PROVIDER_DSP, "provider_id": PROVIDER_ID},
    )

    assert "co2" in text.lower(), (
        f"Expected 'co2' asset IDs in catalog listing. Got:\n{text[:500]}"
    )
    assert "Assets:" in text, f"Expected 'Assets:' count line. Got:\n{text[:500]}"


@pytest.mark.asyncio
async def test_beat5_mcp_ask_dataspace():
    """MCP ask_dataspace tool returns non-empty CO2 data for a CO2 query."""
    text = await _mcp_call("ask_dataspace", {"query": DATA_QUERY})

    assert text.strip() != "No matching data found.", (
        "MCP ask_dataspace found no matching data — check seeding and providers config"
    )
    assert len(text) > 0, "MCP ask_dataspace returned empty text"
    assert "co2" in text.lower() or "scope" in text.lower(), (
        f"MCP ask_dataspace response doesn't look like CO2 data:\n{text[:300]}"
    )


# ── Beat 6: Synthesized answer over live EDC ─────────────────────────────────

CO2_QUERY = "CO2 emissions by German automotive maker 2023"

@pytest.mark.skipif(not _ollama_up(), reason="Ollama not running on localhost:11434")
@pytest.mark.asyncio
async def test_beat6_synthesized_answer():
    """ds.ask() (render default) returns a populated Answer over the live EDC loop."""
    from pythia import Answer

    async with _ds() as ds:
        result = await ds.ask(CO2_QUERY, top_k=3, timeout=60.0)

    assert isinstance(result, Answer), f"Expected Answer, got {type(result)}"
    assert result.table, "Expected non-empty table in synthesized Answer"

    table_text = str(result.table)
    assert "BMW" in table_text or "Volkswagen" in table_text or "VW" in table_text, (
        f"Expected a known OEM maker in table: {result.table}"
    )
    assert result.sources, "Expected non-empty sources in synthesized Answer"
    source_ids = " ".join(s.get("asset_id", "") for s in result.sources)
    assert "co2" in source_ids.lower(), (
        f"Expected a co2 asset id in sources: {result.sources}"
    )
