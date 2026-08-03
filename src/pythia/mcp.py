"""
Pythia MCP server — exposes DataSpace tools to Claude, GPT, and any MCP client.

Tools:
    ask_dataspace   Natural language query → data
    browse_catalog  List available assets across providers

Usage:
    pythia-mcp  (via pyproject.toml [project.scripts])

Or as a library:
    from pythia.mcp import create_server
    server = create_server(management_url="...", api_key="...", providers=[...])
    server.run()

Requires: pip install pythia-edc[mcp]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pythia import DataSpace
from pythia.config import ConnectorConfig, TLSConfig

if TYPE_CHECKING:
    from mcp.server import Server


def create_server(
    management_url: str | None = None,
    api_key: str | None = None,
    api_key_header: str = "X-Api-Key",
    providers: list[dict] | None = None,
    verify_ssl: bool = True,
    ca_bundle: str | None = None,
    client_cert: str | None = None,
    client_key: str | None = None,
) -> Server[Any]:
    try:
        import mcp.server.stdio  # noqa: F401  (checked for availability, used in main())
        from mcp.server import Server, ServerRequestContext
        from mcp.types import (
            CallToolRequestParams,
            CallToolResult,
            ListToolsResult,
            PaginatedRequestParams,
            TextContent,
            Tool,
        )
    except ImportError as exc:
        raise ImportError(
            "MCP server requires mcp package: pip install 'pythia-edc[mcp]'"
        ) from exc

    # Start from env, then let explicit caller args take precedence.
    env_cfg = ConnectorConfig.from_env()
    mgmt_url = management_url or env_cfg.management_url
    key = api_key or env_cfg.api_key
    providers_cfg = providers or env_cfg.providers

    # TLS: explicit kwargs override env-derived config. A caller passing
    # verify_ssl=False disables verification; the default True defers to env.
    tls = TLSConfig(
        verify=env_cfg.tls.verify and verify_ssl,
        ca_bundle=ca_bundle or env_cfg.tls.ca_bundle,
        client_cert=client_cert or env_cfg.tls.client_cert,
        client_key=client_key or env_cfg.tls.client_key,
    )

    async def list_tools(
        ctx: ServerRequestContext[Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        tools = [
            Tool(
                name="ask_dataspace",
                description=(
                    "Query a Gaia-X data space in plain language. "
                    "Discovers providers, ranks available data assets by semantic "
                    "similarity, negotiates a contract, and returns the data. "
                    "No asset IDs, provider URLs, or ODRL knowledge required."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of data you need",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of candidates to try if first fails (default 1)",
                            "default": 1,
                        },
                        "raw": {
                            "type": "boolean",
                            "description": (
                                "By default the data is returned as a readable synthesized "
                                "table with provenance. Set raw=true to return the "
                                "best-matching asset's raw bytes instead."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="browse_catalog",
                description=(
                    "List all data assets available across registered providers. "
                    "Returns asset IDs, titles, descriptions, and provider info."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "provider_dsp": {
                            "type": "string",
                            "description": "DSP URL of a specific provider to query (optional)",
                        },
                        "provider_id": {
                            "type": "string",
                            "description": "Participant ID of a specific provider (optional)",
                        },
                    },
                },
            ),
        ]
        return ListToolsResult(tools=tools)

    def _text(text: str, *, is_error: bool = False) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=text)], is_error=is_error
        )

    async def call_tool(
        ctx: ServerRequestContext[Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        async with DataSpace(
            management_url=mgmt_url,
            api_key=key,
            api_key_header=api_key_header,
            providers=providers_cfg,
            tls=tls,
            max_response_bytes=env_cfg.max_response_bytes,
        ) as ds:
            if name == "ask_dataspace":
                from pythia.synthesize import Answer

                query = arguments["query"]
                top_k = arguments.get("top_k", 1)
                do_raw = bool(arguments.get("raw", False))
                result = await ds.ask(query=query, top_k=top_k, raw=do_raw)
                if result is None:
                    return _text("No matching data found.")
                if isinstance(result, Answer):
                    return _text(result.to_markdown())
                try:
                    text = result.decode("utf-8")
                except UnicodeDecodeError:
                    text = f"[binary data, {len(result)} bytes]"
                return _text(text)

            elif name == "browse_catalog":
                provider_dsp = arguments.get("provider_dsp")
                provider_id = arguments.get("provider_id")

                if provider_dsp and provider_id:
                    providers_to_query = [{"dsp": provider_dsp, "id": provider_id}]
                else:
                    providers_to_query = providers_cfg

                if not providers_to_query:
                    return _text(
                        "No providers configured. Set PYTHIA_PROVIDERS env var.",
                        is_error=True,
                    )

                output = []
                for p in providers_to_query:
                    try:
                        catalog = await ds.catalog.query(
                            provider_dsp=p["dsp"],
                            provider_id=p["id"],
                        )
                        output.append(f"## Provider: {catalog.provider_id}")
                        output.append(f"DSP: {catalog.provider_dsp}")
                        output.append(f"Assets: {len(catalog.assets)}")
                        for asset in catalog.assets:
                            output.append(f"\n### {asset.title or asset.id}")
                            output.append(f"- ID: {asset.id}")
                            if asset.description:
                                output.append(f"- Description: {asset.description}")
                            if asset.keywords:
                                output.append(f"- Keywords: {', '.join(asset.keywords)}")
                            output.append(
                                f"- Offers: {len(asset.offers)} policy offer(s)"
                            )
                    except Exception as e:
                        output.append(f"## Provider: {p['id']} — ERROR: {e}")

                return _text("\n".join(output))

            else:
                return _text(f"Unknown tool: {name}", is_error=True)

    return Server(
        "pythia-edc",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def main() -> None:
    """Entry point for pythia-mcp CLI."""
    import asyncio

    try:
        import mcp.server.stdio
    except ImportError:
        print("MCP server requires: pip install 'pythia-edc[mcp]'")
        raise SystemExit(1)

    server = create_server()

    async def run():
        async with mcp.server.stdio.stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
