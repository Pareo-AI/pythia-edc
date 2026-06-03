<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img alt="Pythia" src="assets/logo.svg" width="440">
  </picture>
</p>

# Pythia: Ask Your Dataspace

> Ask your Gaia-X data space in plain language. MCP-native Python SDK for Eclipse EDC.

---

Pythia is a natural-language and MCP client for Gaia-X / Eclipse EDC data spaces.
Instead of working with provider URLs, asset IDs, ODRL policies, and the EDC
negotiation state machine directly, you ask a question in plain language and
Pythia handles catalog discovery, contract negotiation, transfer, and retrieval.

```python
from pythia import DataSpace

ds = DataSpace(
    management_url="http://consumer:29193/management",
    providers=[{"dsp": "http://provider:19194/protocol/2025-1", "id": "provider"}],
)

# Natural language query — Pythia negotiates, retrieves, and returns a readable
# Answer (a synthesized table + provenance). Pass raw=True for the raw asset bytes.
answer = await ds.ask("CO2 emissions data for German automotive suppliers 2023")
print(answer.to_markdown())
```

It also ships as an **MCP server**, exposing data-space queries as tools for
Claude, GPT, or any MCP-compatible agent.

## Components

| Layer | Component |
|---|---|
| 1 — Core client | Async `httpx` EDC client, full DSP state machine, typed errors, no RabbitMQ |
| 2 — NL interface | `ds.ask()` — parallel catalog fan-out + offline `granite-embedding-97m-multilingual-r2` ranking + auto-negotiate; returns a readable `Answer` by default (synthesized table on success, or an explained refusal in `Answer.note`), `raw=True` for bytes |
| 3 — MCP server | `pythia-mcp` — wraps `ds.ask()` as MCP tools for Claude / GPT / any agent |

## Install

```bash
pip install 'pythia-edc[all]'
```

## Running the demo

The repo ships a self-contained demo stack driven by a single `./demo` entrypoint.

```bash
./demo up                          # start the stack
./demo ask "CO2 emissions for German automotive suppliers 2023"
./demo repl                        # interactive query loop
./demo down                        # stop the stack
```

By default `./demo up` runs everything locally: provider + consumer EDC
connectors, a mock data server, and provider seeding.

### Config profiles

Any setting can live in a profile file instead of inline env vars. `./demo`
loads `demo.env` by default and exports every assignment to both the stack
scripts and the Python client. Copy the example to get started:

```bash
cp demo.env.example demo.env       # edit, then ./demo up
```

Keep several named profiles and select one per run with `ENV_FILE`:

```bash
ENV_FILE=demo.consumer.env ./demo up
```

### Local consumer, remote providers

To run only the consumer locally and talk to providers that run on separate
servers, set `CONSUMER_ONLY=1` (skips the local provider, mock server, and
seeding) and point `PYTHIA_PROVIDERS` at the remote DSP endpoints. See
`demo.env.example` for the full template, including the TLS variables
(`PYTHIA_CA_BUNDLE`, `PYTHIA_CLIENT_CERT`/`KEY`) for talking to remote providers.

```bash
CONSUMER_ONLY=1 \
PYTHIA_PROVIDERS='[{"dsp":"https://provider1.example/protocol/2025-1","id":"rheinmobil"}]' \
./demo up
```

## Usage

```python
# Low-level
catalog = await ds.catalog.query(provider_dsp="...", provider_id="...")
agreement_id = await ds.negotiate(provider_dsp="...", provider_id="...",
                                  offer_id=offer.id, asset_id=asset.id)
data = await ds.fetch(provider_dsp="...", provider_id="...",
                      agreement_id=agreement_id, asset_id=asset.id)

# Natural language — returns a readable Answer (table + provenance)
answer = await ds.ask("quarterly SVHC substance reports from EU chemical suppliers")
print(answer.to_markdown())

# ...or the raw asset bytes, for the developer/agent path
raw = await ds.ask("quarterly SVHC substance reports", raw=True)
```

## Command line

Installing the package puts a `pythia` command on your `PATH`, so you can query a
data space from anywhere:

```bash
# Install globally in an isolated environment (uv) — or use pipx
uv tool install 'pythia-edc[all]'

# Point it at your consumer connector + providers, then ask
export PYTHIA_MANAGEMENT_URL="http://localhost:29193/management"
export PYTHIA_PROVIDERS='[{"dsp": "http://localhost:19194/protocol/2025-1", "id": "provider"}]'

pythia ask "CO2 emissions for German automotive suppliers 2023"
pythia ask "quarterly SVHC reports" --verify-trust --json
```

Connection settings are read from `PYTHIA_*` environment variables (see
`pythia.config.ConnectorConfig`); `--management-url` and `--provider ID DSP`
(repeatable) override them per invocation. Run `pythia ask --help` for all options.

> The repo's `./demo ask` is the zero-config local playground; `pythia ask` is the
> same query path pointed at real connectors you configure.

## MCP server

```bash
export PYTHIA_MANAGEMENT_URL="http://localhost:29193/management"
export PYTHIA_PROVIDERS='[{"dsp": "http://localhost:19194/protocol/2025-1", "id": "provider"}]'
pythia-mcp
```

## Gaia-X standards

| Standard | Usage |
|---|---|
| DSP `dataspace-protocol-http:2025-1` | Full state machine: catalog → negotiate → transfer → EDR |
| DCAT | Catalog parsing, asset/offer extraction |
| ODRL | Policy offer construction with correct context |
| MCP (Model Context Protocol) | AI agent integration (`pythia-mcp` server) |
| Verifiable Credentials + SHACL | Trust slice: verify a provider VC (structure + signature) and validate the offer + ODRL policy against a SHACL shape before negotiating |

### Provider VC verification

When `verify_trust=True` and a `credential_source` is supplied, Pythia verifies each
provider's Verifiable Credential **fully offline** before validating its offer:

- structural validation (required `@context` / `type` / `id` / `issuer` fields, SHACL shape)
- validity-window check (`validFrom` / expiry)
- Ed25519 `JsonWebSignature2020` detached-JWS signature verification via `did:key`
- issuer ↔ signing-key binding (the verifying key must belong to the credential issuer)
- an optional issuer **trust-list** of allowed issuer DIDs

```python
from pythia import DataSpace, StaticCredentialSource

ds.ask("CO2 emissions from German automotive suppliers 2023",
       verify_trust=True,
       credential_source=StaticCredentialSource({"provider": provider_vc}),
       trust_list={"did:key:z6Mk..."})
```

Rejections surface as structured `CredentialError.failures`, rendered into prose by the
Explainer (e.g. "the credential has expired", "the credential issuer is not trusted").

In the **demo**, trust verification is **on by default**: `./demo ask "..."` verifies each
provider's VC and SHACL-validates its offer before negotiating. The demo mints VCs from a
fixed demo CA for the trusted providers, while **DonauTech is intentionally untrusted** —
its VC is signed by an issuer not on the consumer's trust-list, so it is rejected
(`UntrustedIssuer`) and the query falls through to a trusted provider. Disable the gate
with `./demo ask --no-verify-trust "..."`. (The library default of `verify_trust` is
unchanged — off — so non-demo callers opt in explicitly as shown above.)

**Roadmap:** full GXDCH / Notary online credential verification, and resolving **did:web**
issuer DID-documents (the current crypto path effectively trusts `did:key` issuers, since
the verifying key must equal the issuer); provider auto-discovery (the Meta Registry is a
trust-profile source, not a connector directory, so endpoint discovery needs an EDC
FederatedCatalog or a self-description convention).

## Prerequisites

- Python 3.11+, running EDC consumer + provider connectors
- `sentence-transformers` for `ds.ask()` (offline, `ibm-granite/granite-embedding-97m-multilingual-r2`, ~130MB, multilingual)

The local end-to-end demo additionally needs Docker and a checkout of the
[Eclipse EDC Samples](https://github.com/eclipse-edc/Samples) (point `EDC_SAMPLES_DIR` at it).

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the checks to run.

## License

[MIT](LICENSE) © Pareo
