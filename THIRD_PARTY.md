# Third-party material in pythia-edc

pythia-edc is MIT licensed and its source was written for this project. This
file records what came from elsewhere and what that requires.

## Dependencies

Resolved by the installer, never vendored. The wheel contains only this package,
so it carries no third-party notice obligation.

| Package | Extra | License |
| --- | --- | --- |
| httpx | core | BSD-3-Clause |
| pydantic | core | MIT |
| anyio | core | MIT |
| mcp | `mcp` | MIT |
| sentence-transformers | `ask` | Apache-2.0 |
| pyshacl | `trust` | Apache-2.0 |
| rdflib | `trust` | BSD-3-Clause |
| cryptography | `trust` | Apache-2.0 or BSD-3-Clause |
| jcs | `trust` | MIT |
| rich | `cli` | MIT |

## Vocabularies and protocol identifiers

The SDK speaks the Eclipse Dataspace Components management API and the Dataspace
Protocol, so it carries their identifiers: `https://w3id.org/edc/v0.0.1/ns/*`
and `https://w3id.org/dspace/2024/1/*`, plus the JSON member names those
specifications define.

These are the names of things in a published protocol. Using them is what
interoperating means, and it is what specifications are written for. No source
from [eclipse-edc](https://github.com/eclipse-edc) (Apache-2.0) is copied into
this repository. Eclipse, EDC and Gaia-X are trademarks of their respective
holders, used to say which protocol this speaks.

## Demo fixtures

`scripts/demo/` builds a local dataspace from fixtures written for this
repository. Issuer keys are generated from fixed seeds in that code so the demo
is reproducible. They are throwaway test keys and secure nothing.

## Assets

`assets/` holds the Pareo and pythia marks, made for this project.

## Reviewed and cleared

Nothing yet. Findings from `scripts/provenance-check.py` that turn out to be
convergent output rather than copying belong here, with the date and the
reasoning.
