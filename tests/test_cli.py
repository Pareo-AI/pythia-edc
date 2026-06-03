"""Tests for the `pythia` command-line interface.

These exercise argument parsing and output rendering with a fake DataSpace so no
connector or network is required.
"""

from __future__ import annotations

import json

import pytest

from pythia import cli
from pythia.synthesize import Answer


class _FakeDataSpace:
    """Stand-in for DataSpace: records how it was built and called."""

    init_kwargs: dict = {}
    last_query: str | None = None
    last_kwargs: dict = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeDataSpace.init_kwargs = kwargs

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
