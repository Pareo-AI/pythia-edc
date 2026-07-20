"""Pythia command-line interface.

    pythia ask "CO2 emissions for German automotive suppliers 2023"
    pythia mcp                       # run the MCP server (stdio)

Connection settings come from PYTHIA_* environment variables (see
``pythia.config.ConnectorConfig``):

    PYTHIA_MANAGEMENT_URL, PYTHIA_API_KEY, PYTHIA_API_KEY_HEADER,
    PYTHIA_PROVIDERS, PYTHIA_VERIFY_SSL, PYTHIA_CA_BUNDLE, ...

Flags override the environment where given.

Installed as the ``pythia`` console script via pyproject ``[project.scripts]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import DataSpace, __version__
from .ask import DEFAULT_MIN_SCORE
from .config import ConnectorConfig
from .errors import PythiaError
from .synthesize import Answer


def _build_dataspace(args: argparse.Namespace) -> DataSpace:
    """Build a DataSpace from the environment, with CLI flags taking precedence."""
    cfg = ConnectorConfig.from_env()
    providers = _resolve_providers(args, cfg)
    return DataSpace(
        management_url=args.management_url or cfg.management_url,
        api_key=cfg.api_key,
        api_key_header=cfg.api_key_header,
        api_version=cfg.api_version,
        providers=providers,
        timeout=cfg.timeout,
        tls=cfg.tls,
        max_response_bytes=cfg.max_response_bytes,
    )


def _cmd_ask(args: argparse.Namespace) -> int:
    async def run() -> int:
        async with _build_dataspace(args) as ds:
            result = await ds.ask(
                args.query,
                top_k=args.top_k,
                min_score=args.min_score,
                timeout=args.timeout,
                verify_trust=args.verify_trust,
                raw=args.raw,
            )
        if result is None:
            print("No matching dataset found.", file=sys.stderr)
            return 1
        if isinstance(result, Answer):
            if args.json:
                print(
                    json.dumps(
                        {
                            "query": result.query,
                            "table": result.table,
                            "sources": result.sources,
                            "note": result.note,
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(result.to_markdown())
            return 0
        # raw=True (or a non-tabular asset): emit the asset bytes verbatim
        sys.stdout.buffer.write(result)
        return 0

    try:
        return asyncio.run(run())
    except ValueError as exc:  # e.g. no providers configured
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:  # missing optional extra (e.g. pythia-edc[ask])
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except PythiaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _resolve_providers(args: argparse.Namespace, cfg: ConnectorConfig) -> list[dict]:
    """Providers from --provider flags, else $PYTHIA_PROVIDERS."""
    return [{"id": pid, "dsp": dsp} for pid, dsp in (args.provider or [])] or cfg.providers


def _asset_dict(asset) -> dict:  # type: ignore[no-untyped-def]
    return {
        "id": asset.id,
        "title": asset.title,
        "description": asset.description,
        "keywords": asset.keywords,
        "content_type": asset.content_type,
        "offers": len(asset.offers),
    }


def _render_catalogs(results: list[dict]) -> str:
    lines: list[str] = []
    for entry in results:
        header = f"## {entry['provider_id']} ({entry['provider_dsp']})"
        if entry.get("error"):
            lines.append(f"{header} — ERROR: {entry['error']}")
            continue
        assets = entry["assets"]
        lines.append(header)
        lines.append(f"Assets: {len(assets)}")
        for a in assets:
            lines.append(f"\n### {a['title'] or a['id']}")
            lines.append(f"- id: {a['id']}")
            if a["description"]:
                lines.append(f"- description: {a['description']}")
            if a["keywords"]:
                lines.append(f"- keywords: {', '.join(a['keywords'])}")
            if a["content_type"]:
                lines.append(f"- content-type: {a['content_type']}")
            lines.append(f"- offers: {a['offers']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _cmd_catalog(args: argparse.Namespace) -> int:
    async def run() -> int:
        cfg = ConnectorConfig.from_env()
        providers = _resolve_providers(args, cfg)
        if not providers:
            print(
                "error: no providers configured. Pass --provider ID DSP or set "
                "PYTHIA_PROVIDERS.",
                file=sys.stderr,
            )
            return 2

        results: list[dict] = []
        async with _build_dataspace(args) as ds:
            for p in providers:
                try:
                    cat = await ds.catalog.query(provider_dsp=p["dsp"], provider_id=p["id"])
                    results.append(
                        {
                            "provider_id": cat.provider_id,
                            "provider_dsp": cat.provider_dsp,
                            "assets": [_asset_dict(a) for a in cat.assets],
                        }
                    )
                except PythiaError as exc:
                    results.append(
                        {"provider_id": p["id"], "provider_dsp": p["dsp"], "error": str(exc)}
                    )

        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print(_render_catalogs(results))

        # Success if at least one provider's catalog was retrieved.
        return 0 if any("assets" in r for r in results) else 1

    try:
        return asyncio.run(run())
    except PythiaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp import main as mcp_main

    mcp_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pythia",
        description="Ask your Gaia-X data space in plain language.",
    )
    parser.add_argument("--version", action="version", version=f"pythia {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Natural-language query across configured providers")
    ask.add_argument("query", help="The question to ask, in plain language")
    ask.add_argument(
        "--management-url",
        help="Consumer connector Management API URL (default: $PYTHIA_MANAGEMENT_URL)",
    )
    ask.add_argument(
        "--provider",
        nargs=2,
        action="append",
        metavar=("ID", "DSP"),
        help="A provider to query (repeatable). Overrides $PYTHIA_PROVIDERS.",
    )
    ask.add_argument(
        "--verify-trust",
        action="store_true",
        help="Verify each provider's VC and SHACL-validate its offer before negotiating",
    )
    ask.add_argument(
        "--raw",
        action="store_true",
        help="Emit the matched asset's raw bytes instead of a readable table",
    )
    ask.add_argument("--json", action="store_true", help="Emit the Answer as JSON")
    ask.add_argument(
        "--top-k", type=int, default=1, help="Number of top matches to try (default: 1)"
    )
    ask.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help=f"Minimum similarity score to attempt negotiation (default: {DEFAULT_MIN_SCORE})",
    )
    ask.add_argument(
        "--timeout", type=float, default=30.0, help="Per-provider timeout in seconds (default: 30)"
    )
    ask.set_defaults(func=_cmd_ask)

    catalog = sub.add_parser(
        "catalog",
        help="List assets available across providers (no negotiation)",
    )
    catalog.add_argument(
        "--management-url",
        help="Consumer connector Management API URL (default: $PYTHIA_MANAGEMENT_URL)",
    )
    catalog.add_argument(
        "--provider",
        nargs=2,
        action="append",
        metavar=("ID", "DSP"),
        help="A provider to query (repeatable). Overrides $PYTHIA_PROVIDERS.",
    )
    catalog.add_argument("--json", action="store_true", help="Emit the catalog as JSON")
    catalog.set_defaults(func=_cmd_catalog)

    mcp = sub.add_parser("mcp", help="Run the Pythia MCP server (stdio)")
    mcp.set_defaults(func=_cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
