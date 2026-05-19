#!/usr/bin/env python3
"""Query the running demo stack in natural language (via `./demo ask` / `./demo repl`).

    ./demo ask "CO2 emissions for German automotive suppliers"
    ./demo ask --raw "CO2 by maker"          # raw asset bytes instead of a readable table
    ./demo ask --no-verify-trust "CO2 ..."   # skip the trust gate (see below)
    ./demo repl

Connects to the consumer connector from env (PYTHIA_MANAGEMENT_URL, PYTHIA_PROVIDERS,
PYTHIA_API_KEY, TLS via PYTHIA_VERIFY_SSL/CA_BUNDLE/CLIENT_CERT/KEY); falls back to
the local demo connector when those are unset. The stack must be up (`./demo up`).

Trust verification is ON by default in the demo: each provider's Verifiable
Credential is verified and its offer SHACL-validated before negotiating. In the
local-demo setup, DonauTech's credential is intentionally signed by an issuer NOT
on the consumer's trust-list, so it is rejected (UntrustedIssuer) and the query
falls through to a trusted provider. Use `--no-verify-trust` to disable the gate.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Quiet the model-loading progress bar and framework chatter so the rendered
# table is the star of the terminal output. Must be set before any HF/
# transformers import (sentence-transformers loads lazily inside ds.ask()).
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from pythia import DataSpace  # noqa: E402
from pythia.config import ConnectorConfig  # noqa: E402
from pythia.synthesize import Answer  # noqa: E402

# Local-demo fallback only, used when PYTHIA_PROVIDERS is unset. Derived from the
# demo topology (one local connector per logical provider). For remote setups,
# set PYTHIA_PROVIDERS to point at the real providers.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import topology  # noqa: E402

_DEFAULT_PROVIDERS = topology.default_providers()


def _dataspace() -> tuple[DataSpace, bool]:
    """Build the DataSpace from env. Returns ``(ds, is_local_demo)`` where
    ``is_local_demo`` is True when PYTHIA_PROVIDERS was unset and we fell back to
    the local demo providers (the only mode in which the demo attaches its own
    credential source)."""
    cfg = ConnectorConfig.from_env()
    is_local_demo = not cfg.providers
    if is_local_demo:
        print(
            "[ask] PYTHIA_PROVIDERS not set — using local demo provider (localhost:19194).",
            file=sys.stderr,
        )
    ds = DataSpace(
        management_url=cfg.management_url,
        api_key=cfg.api_key,
        api_key_header=cfg.api_key_header,
        api_version=cfg.api_version,
        providers=cfg.providers or _DEFAULT_PROVIDERS,
        tls=cfg.tls,
        timeout=cfg.timeout,
    )
    return ds, is_local_demo


def _rich_console():
    """A Rich console when stdout is an interactive terminal and Rich is installed;
    otherwise None — fall back to plain markdown so piped/redirected output stays
    parseable (and the library/MCP layer is never touched)."""
    if not sys.stdout.isatty():
        return None
    try:
        from rich.console import Console
    except ImportError:
        return None
    return Console()


def _cell(value: object) -> str:
    """Format one table cell — thousands separators for numbers, str for the rest."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return "" if value is None else str(value)


def _column_numeric(table: list[dict], key: str) -> bool:
    vals = [r.get(key) for r in table if r.get(key) is not None]
    return bool(vals) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals
    )


def _display(result: object) -> None:
    console = _rich_console()

    if result is None:
        print("No matching data found.") if console is None else console.print(
            "[yellow]No matching data found.[/]"
        )
        return

    if isinstance(result, bytes):
        try:
            print(result.decode("utf-8"))
        except UnicodeDecodeError:
            print(f"[binary data, {len(result)} bytes]")
        return

    if isinstance(result, Answer):
        if console is None:
            print(result.to_markdown())
        else:
            _display_answer_rich(console, result)
        return

    print(str(result))


def _display_answer_rich(console, answer: Answer) -> None:
    from rich.table import Table

    if answer.table:
        headers = list(answer.table[0].keys())
        table = Table(title=answer.query, header_style="bold", title_style="bold cyan")
        for h in headers:
            table.add_column(
                str(h), justify="right" if _column_numeric(answer.table, h) else "left"
            )
        for row in answer.table:
            table.add_row(*[_cell(row.get(h)) for h in headers])
        console.print(table)
    else:
        console.print("[yellow]No rows returned.[/]")

    if answer.note:
        console.print(f"[yellow]Note:[/] {answer.note}")

    if answer.sources:
        console.print("[dim]Sources:[/]")
        for s in answer.sources:
            title = s.get("title") or "(untitled)"
            console.print(
                f"[dim]  • {s.get('asset_id', '?')} from "
                f"{s.get('provider_id', '?')} — {title}[/]"
            )


async def _ask_once(ds: DataSpace, query: str, **kw: object) -> None:
    _display(await ds.ask(query, timeout=60.0, **kw))


async def _run(
    query: str | None,
    *,
    raw: bool,
    top_k: int,
    verify_trust: bool,
    min_score: float | None,
) -> None:
    kw: dict[str, object] = {"raw": raw, "top_k": top_k, "verify_trust": verify_trust}
    if min_score is not None:
        kw["min_score"] = min_score

    ds, is_local_demo = _dataspace()

    # Attach the demo credential source ONLY in local-demo mode. Pointing at real
    # providers (PYTHIA_PROVIDERS set) must NOT use these demo VCs — every real
    # provider would be rejected as MissingCredential. With no credential_source,
    # the offer SHACL validation still runs; only the provider-VC check is skipped.
    if verify_trust and is_local_demo:
        import credentials  # on sys.path via the lib insert above

        from pythia.credential_source import StaticCredentialSource

        kw["credential_source"] = StaticCredentialSource(credentials.credential_map())
        kw["trust_list"] = credentials.trust_list()

    async with ds:
        if query is not None:
            await _ask_once(ds, query, **kw)
            return
        print("Pythia demo REPL — type a question, or 'quit' to exit.")
        while True:
            try:
                line = input("\nask> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line.lower() in {"quit", "exit", "q"}:
                return
            await _ask_once(ds, line, **kw)


def main() -> None:
    p = argparse.ArgumentParser(prog="demo ask", description="Query the running demo stack.")
    p.add_argument("query", nargs="*", help="Natural-language query (omit for a REPL).")
    p.add_argument(
        "--raw",
        action="store_true",
        help="Return the raw asset bytes instead of a synthesized table.",
    )
    p.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Candidates to try (default 3). >1 lets the query fall through past a "
        "rejected provider (e.g. DonauTech's untrusted credential) to a trusted one.",
    )
    p.add_argument(
        "--verify-trust",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify provider credentials + SHACL-validate offers before negotiating "
        "(default: on; use --no-verify-trust to skip).",
    )
    p.add_argument("--repl", action="store_true", help="Force the interactive REPL.")
    p.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Minimum similarity to count as a match (default from PYTHIA_MIN_SCORE or the lib).",
    )
    args = p.parse_args()

    min_score = args.min_score
    if min_score is None and os.environ.get("PYTHIA_MIN_SCORE"):
        min_score = float(os.environ["PYTHIA_MIN_SCORE"])

    query = " ".join(args.query) if args.query and not args.repl else None
    asyncio.run(
        _run(
            query,
            raw=args.raw,
            top_k=args.top_k,
            verify_trust=args.verify_trust,
            min_score=min_score,
        )
    )


if __name__ == "__main__":
    main()
