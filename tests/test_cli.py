"""Tests for the `pythia` command-line interface.

These exercise argument parsing and output rendering with a fake DataSpace so no
connector or network is required.
"""

from __future__ import annotations

import json

import pytest

from pythia import cli
from pythia.errors import CatalogError
from pythia.models import Catalog, CatalogAsset, PolicyOffer
from pythia.synthesize import Answer


class _FakeCatalogController:
    """Stand-in for ds.catalog: returns a preset Catalog or raises per provider."""

    responses: dict = {}  # provider_id -> Catalog | Exception

    async def query(self, provider_dsp: str, provider_id: str) -> Catalog:
        result = _FakeCatalogController.responses.get(provider_id)
        if isinstance(result, Exception):
            raise result
        assert result is not None, f"no fake catalog for {provider_id!r}"
        return result


class _FakeDataSpace:
    """Stand-in for DataSpace: records how it was built and called."""

    init_kwargs: dict = {}
    last_query: str | None = None
    last_kwargs: dict = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeDataSpace.init_kwargs = kwargs
        self.catalog = _FakeCatalogController()

    async def __aenter__(self) -> _FakeDataSpace:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def ask(self, query: str, **kwargs: object) -> Answer:
        _FakeDataSpace.last_query = query
        _FakeDataSpace.last_kwargs = kwargs
        return Answer(
            query=query,
            table=[{"maker": "BMW", "co2_tonnes": 1890}],
            sources=[{"asset_id": "co2", "provider_id": "bmw", "title": "CO2 Report"}],
            note=None,
        )


def _one_asset_catalog(provider_id: str, dsp: str) -> Catalog:
    return Catalog(
        provider_dsp=dsp,
        provider_id=provider_id,
        assets=[
            CatalogAsset(
                **{"@id": "co2-2023"},
                title="CO2 Emissions 2023",
                description="Annual CO2 data",
                keywords=["co2", "emissions"],
                offers=[PolicyOffer(**{"@id": "offer:co2"})],
            )
        ],
    )


@pytest.fixture
def fake_ds(monkeypatch):
    monkeypatch.setattr(cli, "DataSpace", _FakeDataSpace)
    return _FakeDataSpace


def test_ask_renders_markdown_table(fake_ds, capsys):
    rc = cli.main(["ask", "co2 for suppliers", "--provider", "bmw", "https://p/proto"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "| maker |" in out
    assert "BMW" in out
    assert fake_ds.last_query == "co2 for suppliers"
    assert fake_ds.init_kwargs["providers"] == [{"id": "bmw", "dsp": "https://p/proto"}]


def test_ask_verify_trust_flag_threads_through(fake_ds):
    cli.main(["ask", "q", "--provider", "x", "y", "--verify-trust"])
    assert fake_ds.last_kwargs["verify_trust"] is True


def test_ask_default_does_not_verify_trust(fake_ds):
    cli.main(["ask", "q", "--provider", "x", "y"])
    assert fake_ds.last_kwargs["verify_trust"] is False


def test_ask_json_output(fake_ds, capsys):
    rc = cli.main(["ask", "q", "--provider", "x", "y", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["query"] == "q"
    assert payload["table"][0]["maker"] == "BMW"


def test_missing_subcommand_exits(capsys):
    with pytest.raises(SystemExit):
        cli.main([])


# ── catalog subcommand ────────────────────────────────────────────────────────


def test_catalog_text_output(fake_ds, capsys):
    _FakeCatalogController.responses = {
        "rheinmobil": _one_asset_catalog("rheinmobil", "https://p/proto"),
    }
    rc = cli.main(["catalog", "--provider", "rheinmobil", "https://p/proto"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## rheinmobil (https://p/proto)" in out
    assert "### CO2 Emissions 2023" in out
    assert "- id: co2-2023" in out
    assert "- keywords: co2, emissions" in out
    assert "- offers: 1" in out


def test_catalog_json_output(fake_ds, capsys):
    _FakeCatalogController.responses = {
        "rheinmobil": _one_asset_catalog("rheinmobil", "https://p/proto"),
    }
    rc = cli.main(["catalog", "--provider", "rheinmobil", "https://p/proto", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload[0]["provider_id"] == "rheinmobil"
    assert payload[0]["assets"][0]["id"] == "co2-2023"
    assert payload[0]["assets"][0]["offers"] == 1


def test_catalog_provider_error_is_reported(fake_ds, capsys):
    _FakeCatalogController.responses = {
        "good": _one_asset_catalog("good", "https://g/proto"),
        "bad": CatalogError("Catalog query failed for bad: boom"),
    }
    rc = cli.main([
        "catalog",
        "--provider", "good", "https://g/proto",
        "--provider", "bad", "https://b/proto",
    ])
    out = capsys.readouterr().out
    assert rc == 0  # one provider still succeeded
    assert "### CO2 Emissions 2023" in out
    assert "## bad (https://b/proto) — ERROR:" in out


def test_catalog_all_providers_error_returns_1(fake_ds, capsys):
    _FakeCatalogController.responses = {"bad": CatalogError("boom")}
    rc = cli.main(["catalog", "--provider", "bad", "https://b/proto"])
    assert rc == 1


def test_catalog_no_providers_errors(fake_ds, capsys, monkeypatch):
    monkeypatch.delenv("PYTHIA_PROVIDERS", raising=False)
    rc = cli.main(["catalog"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no providers configured" in err
