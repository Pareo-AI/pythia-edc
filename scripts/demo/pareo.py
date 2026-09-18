#!/usr/bin/env python3
"""Drive the MaterialDigital demonstrator chain end to end (via `./demo pareo`).

    ./demo pareo
    ./demo pareo --pareo-url http://localhost:8000

For each of the three copper alloys the ``kupferwerk`` EDC provider publishes:
fetch its Turtle asset with Pythia's low-level API (catalog → negotiate →
fetch), import it into the Pareo aethon tenant over HTTP, export the product
back out as RDF, run the lead SPARQL competency question over that export,
and print the verdict together with its data-space provenance (asset id +
contract agreement id).

Requires the local demo stack (`./demo up`) and a running Pareo dev backend
(`./scripts/dev.sh up -d` in the Pareo checkout, default `http://localhost:8000`).
The Pareo checkout path is configurable via ``PAREO_REPO`` (default
``/opt/pareo``) — both the competency-question file and the ``.env`` fallback
for credentials are read from there.

LM Studio (optional, `http://localhost:1234/v1`) renders the final verdicts as
one plain-language paragraph. If it is not running, that step is skipped with
a loud notice — the rest of the chain still runs and still prints its verdicts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from rdflib import Graph, URIRef

from pythia import DataSpace
from pythia.config import ConnectorConfig
from pythia.llm import DEFAULT_BASE_URL as LMSTUDIO_BASE_URL
from pythia.llm import LMStudioClient
from pythia.models import negotiation_policy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import datasets  # noqa: E402
import topology  # noqa: E402

PAREO_URL_DEFAULT = "http://localhost:8000"
AETHON_ORG_UUID = "e4af7c27-68c7-4599-9b30-988ac315da1c"
KUPFERWERK_PROVIDER_ID = "kupferwerk"
COMPETENCY_QUESTION_RELPATH = (
    "backend/app/infrastructure/linked_data/ontology/competency_questions/"
    "lead_copper_alloys_exemption.rq"
)

# Mirrors e2e/aethon/constants.py RDF_ROHS_REGULATION_IRI / RDF_REACH_SVHC_REGULATION_IRI —
# duplicated rather than imported, this script never imports the Pareo repo (DEV-1615).
ROHS_REGULATION_IRI = URIRef("https://pareo.ai/data/regulation/rohs")
REACH_SVHC_REGULATION_IRI = URIRef("https://pareo.ai/data/regulation/reach_svhc")


def _pareo_repo() -> Path:
    repo = Path(os.environ.get("PAREO_REPO", "/opt/pareo"))
    if not repo.is_dir():
        raise SystemExit(f"PAREO_REPO={repo} does not exist — set PAREO_REPO to the pareo checkout")
    return repo


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _load_pareo_credentials(pareo_repo: Path) -> tuple[str, str]:
    """E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD from the environment first, then parsed out of
    the Pareo checkout's .env (which — for local — actually names them E2E_LOCAL_ADMIN_*,
    same fallback order as e2e/aethon/env.py). Fails loudly naming both forms if neither
    source has them — no silent default password."""
    email = os.environ.get("E2E_ADMIN_EMAIL")
    password = os.environ.get("E2E_ADMIN_PASSWORD")
    if email and password:
        return email, password

    env_path = pareo_repo / ".env"
    env_vars = _parse_env_file(env_path)
    email = email or env_vars.get("E2E_ADMIN_EMAIL") or env_vars.get("E2E_LOCAL_ADMIN_EMAIL")
    password = (
        password
        or env_vars.get("E2E_ADMIN_PASSWORD")
        or env_vars.get("E2E_LOCAL_ADMIN_PASSWORD")
    )
    if not email or not password:
        raise SystemExit(
            "Missing Pareo credentials: set E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD, or "
            f"E2E_LOCAL_ADMIN_EMAIL / E2E_LOCAL_ADMIN_PASSWORD in {env_path}"
        )
    return email, password


@dataclass(frozen=True)
class Alloy:
    product_number: str
    asset_id: str


def _kupferwerk_alloys() -> list[Alloy]:
    """The kupferwerk provider's datasets, from scripts/demo/lib/datasets.py — the single
    source of truth for asset ids, never hardcoded here."""
    provider = next(p for p in datasets.PROVIDERS if p.id == KUPFERWERK_PROVIDER_ID)
    alloys = []
    for d in provider.datasets:
        assert d.asset_id and d.file_name, f"kupferwerk dataset {d.id!r} missing asset_id/file_name"
        alloys.append(Alloy(product_number=Path(d.file_name).stem, asset_id=d.asset_id))
    return alloys


# ── Pareo HTTP client (shape copied from /opt/pareo/e2e/aethon/live_demo.py::_Client) ──────


class _PareoClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = httpx.Client(follow_redirects=True, timeout=60)
        self._csrf: str | None = None

    def _refresh_csrf(self) -> None:
        r = self.session.get(f"{self.base_url}/api/v1/auth/csrf-token")
        if r.status_code == 200:
            self._csrf = r.json().get("csrf_token")

    def _headers(self) -> dict:
        return {"X-CSRF-Token": self._csrf} if self._csrf else {}

    def login(self, email: str, password: str) -> None:
        self._refresh_csrf()
        r = self.session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=self._headers(),
        )
        if r.status_code != 200:
            raise RuntimeError(f"Pareo login failed: {r.status_code} — {r.text}")
        self._refresh_csrf()

    def switch_org(self, org_uuid: str) -> None:
        r = self.session.post(
            f"{self.base_url}/api/v1/auth/switch-organization",
            json={"organization_uuid": org_uuid},
            headers=self._headers(),
        )
        if r.status_code != 200:
            raise RuntimeError(f"Pareo switch-organization failed: {r.status_code} — {r.text}")
        self._refresh_csrf()

    def import_data_space(
        self,
        *,
        turtle: bytes,
        product_number: str,
        provider: str,
        asset_id: str,
        contract_agreement_id: str,
    ) -> dict:
        r = self.session.post(
            f"{self.base_url}/api/v1/products/import/data-space",
            data={
                "product_number": product_number,
                "provider": provider,
                "asset_id": asset_id,
                "contract_agreement_id": contract_agreement_id,
            },
            files={"file": (f"{product_number}.ttl", turtle, "text/turtle")},
            headers=self._headers(),
        )
        if r.status_code != 201:
            raise RuntimeError(
                f"Pareo import failed for {product_number}: {r.status_code} — {r.text}"
            )
        return r.json()

    def export_rdf(self, product_number: str) -> str:
        r = self.session.get(f"{self.base_url}/api/v1/products/{product_number}/export/rdf")
        if r.status_code != 200:
            raise RuntimeError(
                f"Pareo export failed for {product_number}: {r.status_code} — {r.text}"
            )
        return r.text


# ── Pythia half ──────────────────────────────────────────────────────────────


async def _fetch_asset(
    ds: DataSpace, *, provider_dsp: str, provider_id: str, asset_id: str
) -> tuple[bytes, str]:
    """catalog.query -> pick the offer -> negotiate -> fetch. Returns (turtle bytes,
    contract agreement id)."""
    catalog = await ds.catalog.query(provider_dsp=provider_dsp, provider_id=provider_id)
    asset = next((a for a in catalog.assets if a.id == asset_id), None)
    if asset is None or not asset.offers:
        raise RuntimeError(
            f"asset {asset_id!r} not found (or has no offer) in {provider_id} catalog"
        )
    offer = asset.offers[0]

    # ds.negotiate()'s default policy (built from just offer_id/asset_id) omits the offer's
    # permission/prohibition/obligation. kupferwerk's use-policy has a non-empty `permission`,
    # so the provider rejects the mismatched agreement ("Policy in the contract agreement is
    # not equal to the one in the contract offer") — pass the catalog offer's own raw policy
    # instead of the library default (shared with the ds.negotiate() call sites in
    # pythia.ask, DEV-1615).
    agreement_id = await ds.negotiate(
        provider_dsp=provider_dsp,
        provider_id=provider_id,
        offer_id=offer.id,
        asset_id=asset_id,
        policy=negotiation_policy(offer, provider_id, asset_id),
    )
    data = await ds.fetch(
        provider_dsp=provider_dsp,
        provider_id=provider_id,
        agreement_id=agreement_id,
        asset_id=asset_id,
    )
    return data, agreement_id


# ── Competency question ──────────────────────────────────────────────────────


def _local_name(iri: str) -> str:
    return iri.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _run_lead_question(turtle: str, question_path: Path) -> tuple[str | None, str | None]:
    """Runs lead_copper_alloys_exemption.rq over one product's export. Empty result means the
    alloy triggered neither the RoHS exemption nor the REACH duty (the query's WHERE clause
    requires both, so a passing alloy simply yields no row) — not an error."""
    graph = Graph().parse(data=turtle, format="turtle")
    rows = list(
        graph.query(
            question_path.read_text(),
            initBindings={
                "rohsRegulation": ROHS_REGULATION_IRI,
                "reachRegulation": REACH_SVHC_REGULATION_IRI,
            },
        )
    )
    if not rows:
        return None, None
    row = rows[0]
    exemption_code = str(row.exemptionCode)
    duty = _local_name(str(row.duty))
    return exemption_code, duty


# ── LM Studio plain-language step (optional) ────────────────────────────────


async def _lm_studio_reachable(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base_url}/models")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


async def _plain_language_summary(results: list[dict]) -> str | None:
    if not await _lm_studio_reachable(LMSTUDIO_BASE_URL):
        print(
            "\n"
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            f"!! LM Studio not reachable at {LMSTUDIO_BASE_URL} — SKIPPING the plain-language\n"
            "!! summary step. Everything else above still ran and is correct.\n"
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n",
            file=sys.stderr,
        )
        return None

    prompt = (
        "Structured RoHS/REACH verdicts for three copper alloys (JSON):\n"
        f"{json.dumps(results, indent=2)}\n\n"
        "Restate these verdicts in two or three plain sentences for a non-engineer. "
        "Do not add, invent, or soften any verdict."
    )
    try:
        return await LMStudioClient().generate(
            prompt,
            system=(
                "You restate structured compliance verdicts in plain language. "
                "Never invent or change a verdict."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a renderer failure must never fail the run
        print(f"[pareo] LM Studio call failed, skipping summary: {exc}", file=sys.stderr)
        return None


# ── Orchestration ────────────────────────────────────────────────────────────


async def _run(pareo_url: str) -> int:
    pareo_repo = _pareo_repo()
    question_path = pareo_repo / COMPETENCY_QUESTION_RELPATH
    if not question_path.exists():
        raise SystemExit(f"competency question not found at {question_path} (check PAREO_REPO)")

    email, password = _load_pareo_credentials(pareo_repo)
    alloys = _kupferwerk_alloys()

    cfg = ConnectorConfig.from_env()
    providers = cfg.providers or topology.default_providers()
    kupferwerk = next((p for p in providers if p["id"] == KUPFERWERK_PROVIDER_ID), None)
    if kupferwerk is None:
        raise SystemExit(
            f"no {KUPFERWERK_PROVIDER_ID!r} provider in PYTHIA_PROVIDERS / default topology"
        )

    results: list[dict] = []
    async with DataSpace(
        management_url=cfg.management_url,
        api_key=cfg.api_key,
        api_key_header=cfg.api_key_header,
        api_version=cfg.api_version,
        providers=providers,
        tls=cfg.tls,
        timeout=cfg.timeout,
    ) as ds:
        pareo = _PareoClient(pareo_url)
        pareo.login(email, password)
        pareo.switch_org(AETHON_ORG_UUID)
        print(f"[pareo] logged in to {pareo_url} as {email}, switched to aethon org")

        for alloy in alloys:
            print(f"[pareo] {alloy.product_number}: fetching {alloy.asset_id} from kupferwerk ...")
            turtle, agreement_id = await _fetch_asset(
                ds,
                provider_dsp=kupferwerk["dsp"],
                provider_id=kupferwerk["id"],
                asset_id=alloy.asset_id,
            )
            print(
                f"[pareo] {alloy.product_number}: negotiated agreement {agreement_id}, "
                f"fetched {len(turtle)} bytes"
            )

            pareo.import_data_space(
                turtle=turtle,
                product_number=alloy.product_number,
                provider=KUPFERWERK_PROVIDER_ID,
                asset_id=alloy.asset_id,
                contract_agreement_id=agreement_id,
            )
            export_turtle = pareo.export_rdf(alloy.product_number)
            exemption, duty = _run_lead_question(export_turtle, question_path)
            print(f"[pareo] {alloy.product_number}: imported, exported, competency question run")

            results.append(
                {
                    "alloy": alloy.product_number,
                    "rohs_exemption": exemption,
                    "reach_duty": duty,
                    "asset_id": alloy.asset_id,
                    "contract_agreement_id": agreement_id,
                }
            )

    print()
    header = (
        f"{'alloy':<12}{'RoHS exemption':<18}{'REACH Art. 33 duty':<24}"
        f"{'asset id':<32}contract agreement id"
    )
    print(header)
    for r in results:
        print(
            f"{r['alloy']:<12}{(r['rohs_exemption'] or 'none'):<18}"
            f"{(r['reach_duty'] or 'none'):<24}{r['asset_id']:<32}{r['contract_agreement_id']}"
        )

    summary = await _plain_language_summary(results)
    if summary:
        print("\n" + summary)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="demo pareo",
        description="Run the MaterialDigital demonstrator chain against the local Pareo dev stack.",
    )
    parser.add_argument(
        "--pareo-url",
        default=os.environ.get("PAREO_URL", PAREO_URL_DEFAULT),
        help="Pareo backend base URL (default: %(default)s)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.pareo_url)))


if __name__ == "__main__":
    main()
