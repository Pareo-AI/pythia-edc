"""Offline demo — exercises the Synthesizer and Explainer without any EDC connectors.

Needs only a running Ollama (localhost:11434). Run:

    ./demo offline
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from pythia.explain import LLMExplainer
from pythia.synthesize import FetchedAsset, LLMSynthesizer
from pythia.trust import validate_offer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import datasets

try:
    from pythia.errors import TrustError
except ImportError:  # pragma: no cover
    raise


async def demo_synthesizer() -> None:
    print("\n=== SYNTHESIZER: query -> tabular answer ===")
    # One source per logical provider, all from the single demo source of truth.
    sources = [
        FetchedAsset(
            asset_id=datasets.asset_id(provider, dataset),
            provider_id=provider.id,
            title=dataset.name,
            data=json.dumps(dataset.payload()).encode("utf-8"),
        )
        for provider, dataset in datasets.iter_datasets()
    ]
    answer = await LLMSynthesizer().synthesize(
        "CO2 scope 1/2/3 emissions by German automotive company", sources
    )
    print(answer.to_markdown())


async def demo_explainer() -> None:
    print("\n=== EXPLAINER: SHACL rejection -> plain language ===")
    malformed_offer = {"@id": "offer:abc:malformed", "assigner": "provider"}
    try:
        validate_offer(malformed_offer)
    except TrustError as e:
        prose = await LLMExplainer().explain(e.failures, context="a CO2 emissions offer")
        print(prose)


async def main() -> None:
    await demo_synthesizer()
    await demo_explainer()


if __name__ == "__main__":
    asyncio.run(main())
