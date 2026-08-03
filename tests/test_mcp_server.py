"""
Offline tests for the MCP server surface.

The live demo smoke tests (tests/test_demo_smoke.py) only run against a running
EDC stack, so nothing else in CI exercises pythia.mcp. These tests pin the
wiring against the mcp SDK (handler registration, tool schemas, and the
CallToolResult shape) without needing a connector, by driving only the paths
that return before any DataSpace call.
"""

from __future__ import annotations

import pytest

mcp_types = pytest.importorskip("mcp.types", reason="requires pythia-edc[mcp]")

from pythia.mcp import create_server  # noqa: E402

_TOOL_NAMES = {"ask_dataspace", "browse_catalog"}


def _server():
    return create_server(
        management_url="http://localhost:19193/management",
        api_key="test-key",
        providers=[],
    )


def test_registers_tool_handlers():
    server = _server()

    for method in ("tools/list", "tools/call"):
        assert server.get_request_handler(method) is not None, (
            f"no handler registered for {method}"
        )


@pytest.mark.asyncio
async def test_list_tools_returns_declared_schemas():
    entry = _server().get_request_handler("tools/list")

    result = await entry.handler(None, None)

    assert {t.name for t in result.tools} == _TOOL_NAMES
    for tool in result.tools:
        assert tool.description
        # snake_case since mcp 2.0; camelCase inputSchema is rejected outright.
        assert tool.input_schema["type"] == "object"

    ask = next(t for t in result.tools if t.name == "ask_dataspace")
    assert ask.input_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_call_tool_rejects_unknown_tool():
    entry = _server().get_request_handler("tools/call")
    params = mcp_types.CallToolRequestParams(name="no_such_tool", arguments={})

    result = await entry.handler(None, params)

    assert result.is_error is True
    assert result.content[0].text == "Unknown tool: no_such_tool"


@pytest.mark.asyncio
async def test_browse_catalog_without_providers_reports_error():
    """No providers configured, so this returns before touching the connector."""
    entry = _server().get_request_handler("tools/call")
    params = mcp_types.CallToolRequestParams(name="browse_catalog", arguments={})

    result = await entry.handler(None, params)

    assert result.is_error is True
    assert "PYTHIA_PROVIDERS" in result.content[0].text
