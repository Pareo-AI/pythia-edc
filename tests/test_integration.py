"""
Integration tests — require live EDC connectors.

Run these manually with two EDC JARs started:

    # Terminal 1 (from edc-samples root):
    java -Dedc.fs.config=transfer/transfer-03-consumer-pull/resources/configuration/provider.properties
        -jar transfer/transfer-03-consumer-pull/provider-proxy-data-plane/build/libs/connector.jar

    # Terminal 2:
    java -Dedc.fs.config=transfer/transfer-00-prerequisites/resources/configuration/consumer.properties
        -jar transfer/transfer-00-prerequisites/connector/build/libs/connector.jar

    # Run tests:
    EDC_LIVE=1 uv run python -m pytest tests/test_integration.py -v

These tests are skipped by default (no EDC_LIVE env var).

Covers:
- Catalog query returns assets
- Contract negotiation reaches FINALIZED
- Transfer reaches STARTED
- EDR token retrieved
- Data fetched via EDR
- ds.ask() end-to-end natural language query
"""

import json
import os

import httpx
import pytest
from conftest import (
    API_KEY,
    API_KEY_HEADER,
    CONSUMER_MANAGEMENT,
    PROVIDER_DSP,
    PROVIDER_ID,
    PROVIDER_MANAGEMENT,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("EDC_LIVE"),
    reason="Set EDC_LIVE=1 to run integration tests against live EDC connectors",
)

# ── EDC data seeding (provider-side setup via management API) ─────────────────

ASSET_ID = "pythia-test-co2-2023"
POLICY_ID = "pythia-test-policy"
CONTRACT_DEF_ID = "pythia-test-contract"


async def _seed_provider():
    """Create asset, policy, and contract definition on provider connector."""
    headers = {API_KEY_HEADER: API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        BASE = PROVIDER_MANAGEMENT  # http://localhost:19193/management

        # 1. Create asset
        asset_body = {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "@id": ASSET_ID,
            "properties": {
                "name": "CO2 Emissions 2023",
                "description": "Annual CO2 emissions data for German automotive suppliers",
                "contenttype": "application/json",
            },
            "dataAddress": {
                "type": "HttpData",
                "name": "CO2 data",
                "baseUrl": "https://jsonplaceholder.typicode.com/todos/1",
                "proxyPath": "false",
            },
        }
        r = await client.post(
            f"{BASE}/v3/assets",
            headers=headers,
            json=asset_body,
        )
        # 409 = already exists, OK
        assert r.status_code in (200, 204, 409), f"Asset create failed: {r.status_code} {r.text}"

        # 2. Create policy
        policy_body = {
            "@context": {
                "@vocab": "https://w3id.org/edc/v0.0.1/ns/",
                "odrl": "http://www.w3.org/ns/odrl/2/",
            },
            "@id": POLICY_ID,
            "policy": {
                "@context": "http://www.w3.org/ns/odrl.jsonld",
                "@type": "Set",
                "permission": [],
                "prohibition": [],
                "obligation": [],
            },
        }
        r = await client.post(
            f"{BASE}/v3/policydefinitions",
            headers=headers,
            json=policy_body,
        )
        assert r.status_code in (200, 204, 409), f"Policy create failed: {r.status_code} {r.text}"

        # 3. Create contract definition
        contract_body = {
            "@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"},
            "@id": CONTRACT_DEF_ID,
            "accessPolicyId": POLICY_ID,
            "contractPolicyId": POLICY_ID,
            "assetsSelector": [
                {
                    "operandLeft": "https://w3id.org/edc/v0.0.1/ns/id",
                    "operator": "=",
                    "operandRight": ASSET_ID,
                }
            ],
        }
        r = await client.post(
            f"{BASE}/v3/contractdefinitions",
            headers=headers,
            json=contract_body,
        )
        assert r.status_code in (200, 204, 409), (
            f"Contract def create failed: {r.status_code} {r.text}"
        )


@pytest.fixture(scope="module", autouse=True)
async def seed_provider():
    """Seed provider with test data before integration tests."""
    await _seed_provider()
    yield


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connector_health():
    """Both connectors respond on management port (any non-connection-error = alive)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        for url in [PROVIDER_MANAGEMENT, CONSUMER_MANAGEMENT]:
            # POST /v3/assets/request with empty body → 400/422 means the connector is alive
            r = await client.post(
                f"{url}/v3/assets/request",
                json={"@context": {"@vocab": "https://w3id.org/edc/v0.0.1/ns/"}},
                headers={API_KEY_HEADER: API_KEY, "Content-Type": "application/json"},
            )
            assert r.status_code < 500, (
                f"Connector not healthy: {url} → {r.status_code} {r.text[:100]}"
            )


@pytest.mark.asyncio
async def test_catalog_query():
    """Catalog query returns at least one asset with expected ID."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        catalog = await ds.catalog.query(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
        )

    assert len(catalog.assets) >= 1
    ids = [a.id for a in catalog.assets]
    assert ASSET_ID in ids, f"Expected {ASSET_ID} in catalog, got: {ids}"

    asset = next(a for a in catalog.assets if a.id == ASSET_ID)
    assert asset.title == "CO2 Emissions 2023"
    assert len(asset.offers) >= 1


@pytest.mark.asyncio
async def test_full_negotiation():
    """Contract negotiation reaches FINALIZED state."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        catalog = await ds.catalog.query(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
        )
        asset = next(a for a in catalog.assets if a.id == ASSET_ID)
        offer = asset.offers[0]

        agreement_id = await ds.negotiate(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
            offer_id=offer.id,
            asset_id=ASSET_ID,
            timeout=30.0,
        )

    assert agreement_id, "Expected non-empty agreement_id"
    assert isinstance(agreement_id, str)


@pytest.mark.asyncio
async def test_full_transfer_and_edr():
    """Transfer reaches STARTED and EDR token is retrievable."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        catalog = await ds.catalog.query(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
        )
        asset = next(a for a in catalog.assets if a.id == ASSET_ID)
        offer = asset.offers[0]

        agreement_id = await ds.negotiate(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
            offer_id=offer.id,
            asset_id=ASSET_ID,
            timeout=30.0,
        )

        edr = await ds.transfer(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
            agreement_id=agreement_id,
            asset_id=ASSET_ID,
            timeout=30.0,
        )

    assert edr.endpoint.startswith("http")
    assert edr.authorization
    assert "Authorization" in edr.headers


@pytest.mark.asyncio
async def test_full_data_fetch():
    """End-to-end: negotiate → transfer → EDR → fetch actual data bytes."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        catalog = await ds.catalog.query(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
        )
        asset = next(a for a in catalog.assets if a.id == ASSET_ID)
        offer = asset.offers[0]

        data = await ds.fetch(
            provider_dsp=PROVIDER_DSP,
            provider_id=PROVIDER_ID,
            agreement_id=await ds.negotiate(
                provider_dsp=PROVIDER_DSP,
                provider_id=PROVIDER_ID,
                offer_id=offer.id,
                asset_id=ASSET_ID,
            ),
            asset_id=ASSET_ID,
        )

    assert data
    # Should be JSON from jsonplaceholder.typicode.com/todos/1
    parsed = json.loads(data)
    assert "id" in parsed or "title" in parsed, f"Unexpected data: {data[:200]}"


@pytest.mark.asyncio
async def test_ask_natural_language():
    """ds.ask() with CO2 query retrieves data without explicit asset IDs."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        data = await ds.ask(
            "CO2 emissions data for German automotive suppliers",
            timeout=30.0,
            raw=True,
        )

    assert data is not None, "ds.ask() returned None — no matching asset found"
    assert len(data) > 0


@pytest.mark.asyncio
async def test_ask_paraphrase():
    """ds.ask() with paraphrase of CO2 query still retrieves data."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        data = await ds.ask(
            "greenhouse gas output for car manufacturers in Germany",
            timeout=30.0,
            raw=True,
        )

    assert data is not None, "Paraphrase query returned None"


@pytest.mark.asyncio
async def test_ask_no_match_returns_none():
    """ds.ask() returns None for completely unrelated query."""
    from pythia import DataSpace

    async with DataSpace(
        management_url=CONSUMER_MANAGEMENT,
        api_key=API_KEY,
        providers=[{"dsp": PROVIDER_DSP, "id": PROVIDER_ID}],
    ) as ds:
        data = await ds.ask(
            "medieval castle floor plans and blueprints",
            min_score=0.8,  # very high threshold
            timeout=30.0,
            raw=True,
        )

    assert data is None, "Expected None for unrelated query with high min_score"
