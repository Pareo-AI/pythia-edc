# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (pre-1.0: minor
versions may include breaking changes).

## [0.3.0] - 2026-07-20

### Changed
- **Local LLM runtime is now LM Studio (OpenAI-compatible), not Ollama.** The
  synthesizer/explainer client is `pythia.llm.LMStudioClient`, targeting
  `http://localhost:1234/v1/chat/completions`. Override with `PYTHIA_LLM_BASE_URL`
  / `PYTHIA_LLM_MODEL`. The default model id is `google/gemma-4-e4b` (the id LM
  Studio serves at `/v1/models`). **Breaking:** `OllamaClient` is removed.
- Upgraded the locked dependency graph to clear known advisories reported by
  `pip-audit` (cryptography, mcp, starlette, torch, setuptools, pydantic-settings).

### Added
- **`pythia catalog`** CLI command — list assets across configured providers
  without negotiating (mirrors the MCP `browse_catalog` tool); supports
  `--provider`, `--management-url`, and `--json`.
- **Response-size cap** (memory-DoS guard): provider responses on both the
  data-plane fetch and the management/catalog plane are streamed and aborted past
  a limit. Configurable via `max_response_bytes` (`DataSpace`) or
  `PYTHIA_MAX_RESPONSE_BYTES`; default 100 MiB.
- Release automation: `publish.yml` (tag `v*` → PyPI via Trusted Publishing, with
  a tag-vs-version guard), `pip-audit` and coverage in CI, and Dependabot.

### Documentation
- README now documents the LM Studio prerequisite for the default synthesized
  answer, the `pythia catalog` command, and the response-size cap.

## [0.2.0] - 2026-05-29

- Initial public release: async Eclipse EDC client (full DSP state machine),
  natural-language `ds.ask()` with offline embedding ranking, MCP server, and the
  Verifiable-Credential + SHACL trust slice.
