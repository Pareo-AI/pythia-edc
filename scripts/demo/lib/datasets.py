"""Single source of truth for Pythia's demo data.

Defines logical providers and the datasets each one exposes. Most datasets
share the CO2 emissions schema (GHG Protocol scope 1/2/3, JSON); kupferwerk's
are pre-authored Turtle (RDF) files served verbatim instead (see
``Dataset.file_name``/``content_type``). Consumed by:

  - lib/mock_server.py  serves the JSON payloads over HTTP
  - lib/seed.py         derives EDC asset / catalog metadata
  - offline.py          builds FetchedAsset inputs for the synthesizer

One EDC connector hosts every provider; "providers" here are logical tenants,
distinguished by their asset-id prefix and their mock-server URL path
(``/{provider}/{dataset}``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

MOCK_BASE_URL = os.environ.get("PYTHIA_MOCK_BASE_URL", "http://localhost:9876")
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UNIT = "tonnes CO2e"
STANDARD = "GHG Protocol Corporate Standard"


@dataclass(frozen=True)
class EmissionRow:
    supplier: str
    bpnl: str
    location: str
    sector: str
    scope1: int
    scope2: int
    scope3: int

    @property
    def total(self) -> int:
        return self.scope1 + self.scope2 + self.scope3

    def to_dict(self) -> dict:
        return {
            "supplier": self.supplier,
            "bpnl": self.bpnl,
            "location": self.location,
            "sector": self.sector,
            "scope1": self.scope1,
            "scope2": self.scope2,
            "scope3": self.scope3,
            "total": self.total,
        }


def _split(
    supplier: str,
    bpnl: str,
    location: str,
    sector: str,
    total: int,
    s1: float = 0.08,
    s2: float = 0.06,
) -> EmissionRow:
    """Build a row from a known total, splitting it across scopes (scope3 = remainder)."""
    scope1 = round(total * s1)
    scope2 = round(total * s2)
    scope3 = total - scope1 - scope2
    return EmissionRow(supplier, bpnl, location, sector, scope1, scope2, scope3)


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    description: str
    period: str
    rows: tuple[EmissionRow, ...]
    # Override for datasets.asset_id() — set when the supplier's own file already
    # declares the id a downstream consumer must match (e.g. an RDF `preg:assetId`).
    # Unset datasets keep the derived "{provider.id}_{dataset.id}" id.
    asset_id: str | None = None
    # Non-JSON assets: content_type overrides the "application/json" default and
    # file_name names a verbatim file under lib/data/ served instead of payload().
    content_type: str = "application/json"
    file_name: str | None = None

    def payload(self) -> dict:
        return {
            "dataset": self.name,
            "unit": UNIT,
            "standard": STANDARD,
            "period": self.period,
            "entries": [r.to_dict() for r in self.rows],
            "summary": {
                "total_scope1": sum(r.scope1 for r in self.rows),
                "total_scope2": sum(r.scope2 for r in self.rows),
                "total_scope3": sum(r.scope3 for r in self.rows),
                "grand_total": sum(r.total for r in self.rows),
                "suppliers_reporting": len(self.rows),
            },
        }


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    datasets: tuple[Dataset, ...]


# ── The data ──────────────────────────────────────────────────────────────────

PROVIDERS: tuple[Provider, ...] = (
    Provider(
        id="rheinmobil",
        name="RheinMobil Automotive",
        datasets=(
            Dataset(
                id="co2_oem_2023",
                name="CO2 Emissions of German Car Manufacturers (Automotive OEMs) 2023",
                description=(
                    "Scope 1, 2 and 3 CO2 emissions in tonnes for German car manufacturers — the "
                    "automotive OEMs and carmakers Volkswagen, BMW, Mercedes-Benz, Audi, Porsche "
                    "and Opel that build finished vehicles. Annual greenhouse-gas output of "
                    "Germany's vehicle-manufacturing brands under the GHG Protocol."
                ),
                period="2023",
                rows=(
                    _split("Volkswagen", "BPNL000000010001", "Wolfsburg", "Automotive OEM", 23_400_000),
                    _split("BMW", "BPNL000000010002", "Munich", "Automotive OEM", 18_900_000),
                    _split("Mercedes-Benz", "BPNL000000010003", "Stuttgart", "Automotive OEM", 17_200_000),
                    _split("Audi", "BPNL000000010004", "Ingolstadt", "Automotive OEM", 9_800_000),
                    _split("Porsche", "BPNL000000010005", "Stuttgart", "Automotive OEM", 3_100_000),
                    _split("Opel", "BPNL000000010006", "Rüsselsheim", "Automotive OEM", 4_600_000),
                ),
            ),
        ),
    ),
    Provider(
        id="zugspitze",
        name="Zugspitze Components",
        datasets=(
            Dataset(
                id="co2_suppliers_2023",
                name="CO2 Emissions of German Automotive Parts Suppliers 2023",
                description=(
                    "Scope 1, 2 and 3 CO2 emissions in tonnes for leading German automotive parts "
                    "and component suppliers in 2023 — the tier-1 supply-chain partners Bosch, "
                    "Continental, ZF Friedrichshafen, Schaeffler and Mahle. Annual greenhouse-gas "
                    "output of Germany's automotive components and parts-supply industry."
                ),
                period="2023",
                rows=(
                    _split("Bosch", "BPNL000000020001", "Gerlingen", "Mobility Components", 8_700_000),
                    _split("Continental", "BPNL000000020002", "Hanover", "Tires & Components", 5_300_000),
                    _split("ZF Friedrichshafen", "BPNL000000020003", "Friedrichshafen", "Driveline Systems", 4_100_000),
                    _split("Schaeffler", "BPNL000000020004", "Herzogenaurach", "Bearings & Drives", 2_900_000),
                    _split("Mahle", "BPNL000000020005", "Stuttgart", "Thermal Management", 1_800_000),
                ),
            ),
        ),
    ),
    Provider(
        id="donautech",
        name="DonauTech Supply Base",
        datasets=(
            Dataset(
                id="co2_supplybase_2023",
                name="CO2 Emissions of the German Automotive Tier-2 Supply Base 2023",
                description=(
                    "Detailed scope 1, 2 and 3 CO2 emissions in tonnes for tier-2 German automotive "
                    "supply-base partners in 2023, with BPNL identifiers, locations and sectors. "
                    "GHG Protocol Corporate Standard reporting for Germany's deeper automotive supply base."
                ),
                period="2023",
                rows=(
                    EmissionRow("AutoTech GmbH", "BPNL000000001234", "Stuttgart", "Powertrain Components", 12_400, 8_200, 145_000),
                    EmissionRow("Precision Parts AG", "BPNL000000005678", "Munich", "Metal Stamping", 8_900, 6_100, 98_000),
                    EmissionRow("Bavaria Motors KG", "BPNL000000009012", "Regensburg", "Electric Drive Systems", 21_000, 14_500, 312_000),
                    EmissionRow("NordMetall GmbH", "BPNL000000003456", "Hamburg", "Aluminium Casting", 5_600, 4_200, 67_000),
                    EmissionRow("RheinPlastic SE", "BPNL000000007890", "Düsseldorf", "Interior Components", 3_200, 2_800, 44_000),
                    EmissionRow("Sachsen Elektronik GmbH", "BPNL000000002345", "Dresden", "Power Electronics", 1_800, 3_400, 28_500),
                ),
            ),
        ),
    ),
    # Turtle assets, not the shared CO2 JSON schema: three copper-alloy material
    # compositions in PMDco / pareo-regulatory terms. Files are byte-for-byte
    # copies of Pareo's pareo-regulatory ontology examples
    # (backend/app/infrastructure/linked_data/ontology/examples/*.ttl) — a real
    # supplier owns and republishes its own files, so a copy is correct here and
    # keeps this repo runnable standalone.
    Provider(
        id="kupferwerk",
        name="Kupferwerk Odenwald",
        datasets=(
            Dataset(
                id="cuzn39pb3_composition",
                name="Material Composition — CuZn39Pb3 (CW614N)",
                description=(
                    "Material composition of the leaded brass alloy CuZn39Pb3 (CW614N), as an "
                    "RDF graph in PMDco / pareo-regulatory terms: copper, zinc and lead mass "
                    "fractions plus a claimed RoHS Annex III exemption (6c) for the lead content. "
                    "Published as a Turtle (text/turtle) linked-data asset, not JSON."
                ),
                period="current",
                rows=(),
                asset_id="asset-cuzn39pb3-composition",
                content_type="text/turtle",
                file_name="CuZn39Pb3.ttl",
            ),
            Dataset(
                id="cuzn37_composition",
                name="Material Composition — CuZn37 (CW508L)",
                description=(
                    "Material composition of the brass alloy CuZn37 (CW508L), as an RDF graph in "
                    "PMDco / pareo-regulatory terms: copper and zinc mass fractions. Published as "
                    "a Turtle (text/turtle) linked-data asset, not JSON."
                ),
                period="current",
                rows=(),
                asset_id="asset-cuzn37-composition",
                content_type="text/turtle",
                file_name="CuZn37.ttl",
            ),
            Dataset(
                id="cusn6_composition",
                name="Material Composition — CuSn6 (CW452K)",
                description=(
                    "Material composition of the tin bronze alloy CuSn6 (CW452K), as an RDF graph "
                    "in PMDco / pareo-regulatory terms: copper, tin and phosphorus mass fractions. "
                    "Published as a Turtle (text/turtle) linked-data asset, not JSON."
                ),
                period="current",
                rows=(),
                asset_id="asset-cusn6-composition",
                content_type="text/turtle",
                file_name="CuSn6.ttl",
            ),
        ),
    ),
)


# ── Derived views ─────────────────────────────────────────────────────────────

def iter_datasets():
    """Yield (provider, dataset) for every dataset across all providers."""
    for provider in PROVIDERS:
        for dataset in provider.datasets:
            yield provider, dataset


def asset_id(provider: Provider, dataset: Dataset) -> str:
    return dataset.asset_id or f"{provider.id}_{dataset.id}"


def mock_path(provider: Provider, dataset: Dataset) -> str:
    return f"{provider.id}/{dataset.id}"


def asset_url(provider: Provider, dataset: Dataset) -> str:
    return f"{MOCK_BASE_URL}/{mock_path(provider, dataset)}"


def raw_bytes(dataset: Dataset) -> bytes:
    """The dataset's asset bytes: its file's contents verbatim if file-backed
    (``file_name`` set), else its JSON payload encoded as utf-8."""
    if dataset.file_name is not None:
        with open(os.path.join(_DATA_DIR, dataset.file_name), "rb") as f:
            return f.read()
    return json.dumps(dataset.payload()).encode("utf-8")


def find_dataset(path: str) -> Dataset | None:
    """Look up a dataset by its mock path ('provider/dataset'). None if unknown."""
    for provider, dataset in iter_datasets():
        if mock_path(provider, dataset) == path:
            return dataset
    return None


def find_payload(path: str) -> dict | None:
    """Look up a dataset's JSON payload by its mock path. None if unknown or file-backed."""
    dataset = find_dataset(path)
    if dataset is None or dataset.file_name is not None:
        return None
    return dataset.payload()


def all_mock_paths() -> list[str]:
    return [mock_path(p, d) for p, d in iter_datasets()]
