# Contributing to Pythia

Thanks for your interest in improving Pythia! Contributions of all kinds are
welcome — bug reports, fixes, docs, and features.

## Development setup

Pythia uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
git clone https://github.com/Pareo-AI/pythia-edc.git
cd pythia-edc
uv sync --extra dev --extra all
```

## Checks

Before opening a pull request, make sure the standard checks pass:

```bash
uv run ruff check .       # lint
uv run ruff format --check .
uv run mypy src           # type-check
uv run pytest             # tests
```

The local end-to-end demo (requires Docker and an `eclipse-edc/Samples`
checkout) can be exercised with:

```bash
./demo verify
```

## Pull requests

- Keep changes focused; one logical change per PR.
- Add or update tests for behaviour changes.
- Follow the existing code style (enforced by `ruff`).
- Describe the motivation and any user-facing impact in the PR description.

## Reporting issues

Please open issues at https://github.com/Pareo-AI/pythia-edc/issues with steps
to reproduce, expected vs. actual behaviour, and your environment (OS, Python
version).
