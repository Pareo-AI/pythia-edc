"""
Natural language interface for data space queries.

Uses sentence-transformers (ibm-granite/granite-embedding-97m-multilingual-r2,
~130MB, fully offline) to rank EDC asset catalog descriptions against a natural
language query, then auto-negotiates and fetches the best match.

By default ``ask()`` returns a readable :class:`~pythia.synthesize.Answer`: on
success a synthesized table + provenance; on a trust refusal or empty result an
``Answer`` whose ``note`` carries the plain-language reason (rendered by the
Explainer). Pass ``raw=True`` to get the best-matching asset's raw bytes instead
(the developer/agent contract, and the automatic path for non-tabular/binary
assets). Synthesis and explanation share one local renderer, so both stay offline.

Model choice: benchmarked against MTEB leaderboard for small models (≤150M params).
granite-embedding-97m-multilingual-r2 scores highest on retrieval (60.32 vs 36.26
for bge-small-en-v1.5) and wins on all 4 benchmark queries. Multilingual support
is a bonus for international Gaia-X deployments (German, French, etc.).

Validated: 4/4 correct across exact + paraphrase queries (2026-05-25).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import Catalog, CatalogAsset

if TYPE_CHECKING:
    from . import DataSpace
    from .credential_source import CredentialSource
    from .errors import TrustFailure
    from .explain import Explainer
    from .synthesize import Answer, Synthesizer

_MODEL_NAME = "ibm-granite/granite-embedding-97m-multilingual-r2"
_model = None  # lazy-loaded singleton

# granite cos-sim scores compress high: calibration on the demo catalog put
# off-topic queries (pizza, weather, sourdough) at 0.66–0.79 and on-topic ones
# at ≥0.90, so 0.82 separates them. Below this we treat the query as unmatched
# and return None rather than dumping the nearest dataset. Override per call.
DEFAULT_MIN_SCORE = 0.82


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


@dataclass
class RankedAsset:
    score: float
    asset: CatalogAsset
    catalog: Catalog
    rank: int = 0


def rank_assets(query: str, catalogs: list[Catalog]) -> list[RankedAsset]:
    """
    Rank all assets from all catalogs against a natural language query.

    Returns list of RankedAsset sorted by score descending.
    """
    from sentence_transformers import util

    model = _get_model()

    all_assets: list[tuple[CatalogAsset, Catalog]] = []
    descriptions: list[str] = []

    for catalog in catalogs:
        for asset in catalog.assets:
            if not asset.offers:
                continue  # skip assets with no offers (can't negotiate)
            title = asset.title or asset.id
            desc = asset.description or ""
            text = f"{title}. {desc}".strip()
            all_assets.append((asset, catalog))
            descriptions.append(text)

    if not all_assets:
        return []

    query_emb = model.encode(query, convert_to_tensor=True)
    asset_embs = model.encode(descriptions, convert_to_tensor=True)
    scores = util.cos_sim(query_emb, asset_embs)[0].tolist()

    ranked = sorted(
        zip(scores, all_assets),
        key=lambda x: x[0],
        reverse=True,
    )

    return [
        RankedAsset(score=round(s, 4), asset=a, catalog=c, rank=i)
        for i, (s, (a, c)) in enumerate(ranked)
    ]


class AskController:
    def __init__(
        self,
        ds: DataSpace,  # type: ignore[name-defined]
        explainer: Explainer | None = None,  # type: ignore[name-defined]
        synthesizer: Synthesizer | None = None,  # type: ignore[name-defined]
        credential_source: CredentialSource | None = None,  # type: ignore[name-defined]
    ) -> None:
        self.ds = ds
        self.explainer = explainer
        self.synthesizer = synthesizer
        self.credential_source = credential_source

    # ── Shared rendering of trust verdicts ──────────────────────────────────────

    async def _explain(
        self, failures: list[TrustFailure], *, context: str  # type: ignore[name-defined]
    ) -> str:
        """Render structured trust failures into prose.

        Defaults to the offline, deterministic ``TemplateExplainer`` so a refusal
        reason is always human-readable without any model or network. A caller can
        inject ``LLMExplainer`` for richer prose.
        """
        explainer = self.explainer
        if explainer is None:
            from .explain import TemplateExplainer
            explainer = TemplateExplainer()
        return await explainer.explain(failures, context=context)

    async def _provider_trust_reason(
        self,
        catalog: Catalog,
        trust_list: set[str] | None,
        cache: dict[str, str | None],
    ) -> str | None:
        """Resolve and verify the provider's VC.

        Returns ``None`` when the provider is trusted (or no credential_source is
        configured), or a plain-language rejection reason when its VC is
        missing/invalid. The verdict (reason or None) is cached per provider.
        """
        from .credential import verify_credential
        from .errors import CredentialError, TrustFailure

        provider_id = catalog.provider_id
        if provider_id in cache:
            return cache[provider_id]

        # Callers gate this on `self.credential_source is not None` (see the _gather helpers).
        assert self.credential_source is not None
        vc = await self.credential_source.resolve(
            {"id": provider_id, "dsp": catalog.provider_dsp}
        )
        if vc is None:
            failures = [
                TrustFailure(
                    message="provider presented no verifiable credential",
                    constraint="MissingCredential",
                )
            ]
            reason = await self._explain(failures, context=f"provider {provider_id}")
            print(f"[pythia] provider {provider_id} rejected: {reason}")
            cache[provider_id] = reason
            return reason

        try:
            verify_credential(vc, trust_list=trust_list)
        except CredentialError as e:
            failures = e.failures or [
                TrustFailure(message=str(e), constraint="CredentialError")
            ]
            reason = await self._explain(failures, context=f"provider {provider_id}")
            print(f"[pythia] provider {provider_id} rejected: {reason}")
            cache[provider_id] = reason
            return reason

        print(f"[pythia] provider {provider_id} credential verified ✓")
        cache[provider_id] = None
        return None

    async def _offer_trust_reason(
        self, offer, asset: CatalogAsset, catalog: Catalog
    ) -> str | None:
        """Validate the offer + ODRL policy against the SHACL shape.

        Returns ``None`` when the offer conforms, or a plain-language rejection
        reason otherwise.
        """
        from .errors import TrustError
        from .trust import validate_offer

        try:
            validate_offer(offer.raw, target=offer.target)
            print(f"[pythia] offer for {asset.id!r} conforms to trust shape ✓")
            return None
        except TrustError as e:
            if e.failures:
                reason = await self._explain(
                    e.failures,
                    context=f"offer {asset.id!r} from {catalog.provider_id}",
                )
            else:
                reason = str(e)
            print(f"[pythia] offer for {asset.id!r} rejected: {reason}")
            return reason

    async def _fetch_catalog(self, provider: dict) -> Catalog | None:
        """Fetch catalog from a single provider, swallowing errors."""
        try:
            return await self.ds.catalog.query(
                provider_dsp=provider["dsp"],
                provider_id=provider["id"],
            )
        except Exception as e:
            print(f"[pythia] catalog query failed for {provider['id']}: {e}")
            return None

    def _empty_answer(self, query: str, note: str) -> Answer:  # type: ignore[name-defined]
        from .synthesize import Answer

        return Answer(query=query, table=[], sources=[], note=note)

    # ── Entry point ─────────────────────────────────────────────────────────────

    async def ask(
        self,
        query: str,
        providers: list[dict],
        top_k: int = 1,
        min_score: float = DEFAULT_MIN_SCORE,
        timeout: float = 30.0,
        verify_trust: bool = False,
        trust_list: set[str] | None = None,
        raw: bool = False,
        synthesizer: Synthesizer | None = None,  # type: ignore[name-defined]
    ) -> bytes | Answer | None:  # type: ignore[name-defined]
        """
        Fan out catalog queries, rank, negotiate, fetch.

        Args:
            query:        Natural language query
            providers:    List of {"dsp": "...", "id": "..."} dicts
            top_k:        Try up to this many candidates if first fails
            min_score:    Skip candidates below this similarity score
            verify_trust: Validate each offer against a SHACL shape before negotiating
            trust_list:   Allowed issuer DIDs for provider-VC verification (when a
                          credential_source is configured)
            raw:          Return the best-matching asset's raw bytes instead of a
                          synthesized Answer. Default False (render a readable Answer).
            synthesizer:  Synthesizer instance; defaults to LLMSynthesizer()

        Returns:
            By default an Answer (synthesized table + sources on success; an empty
            table with a ``note`` explaining the refusal/miss otherwise). With
            raw=True, the best-matching asset's bytes, or None if nothing matched.
            Non-tabular/binary assets fall back to raw bytes even when raw=False.
        """
        print(f"[pythia] querying {len(providers)} provider(s) for: {query!r}")
        if verify_trust:
            print(
                "[pythia] trust verification ON"
                + (f" — {len(trust_list)} trusted issuer(s)" if trust_list else "")
            )

        # Fan out catalog queries in parallel
        catalog_tasks = [self._fetch_catalog(p) for p in providers]
        results = await asyncio.gather(*catalog_tasks)
        catalogs = [c for c in results if c is not None]

        if not catalogs:
            print("[pythia] no providers reachable")
            return None if raw else self._empty_answer(
                query, "No data-space providers were reachable."
            )

        total_assets = sum(len(c.assets) for c in catalogs)
        print(f"[pythia] ranking {total_assets} asset(s) across {len(catalogs)} provider(s)")

        ranked = rank_assets(query, catalogs)

        if not ranked:
            print("[pythia] no assets found in any catalog")
            return None if raw else self._empty_answer(
                query, "No assets were found in any provider catalog."
            )

        if raw:
            return await self._ask_raw(
                query, ranked, top_k, min_score, timeout, verify_trust, trust_list
            )
        return await self._ask_render(
            query, ranked, top_k, min_score, timeout, verify_trust, trust_list,
            synthesizer or self.synthesizer,
        )

    # ── Raw path: return the best-matching asset's bytes ────────────────────────

    async def _ask_raw(
        self,
        query: str,
        ranked: list[RankedAsset],
        top_k: int,
        min_score: float,
        timeout: float,
        verify_trust: bool,
        trust_list: set[str] | None,
    ) -> bytes | None:
        vc_cache: dict[str, str | None] = {}
        attempted = 0
        for candidate in ranked[:top_k]:
            if candidate.score < min_score:
                print(
                    f"[pythia] best match scored {candidate.score:.3f} < min_score "
                    f"{min_score} — no relevant data found"
                )
                break

            asset = candidate.asset
            catalog = candidate.catalog
            offer = asset.offers[0]

            print(
                f"[pythia] top match: {asset.title or asset.id!r} "
                f"(score={candidate.score:.3f}, provider={catalog.provider_id})"
            )

            if verify_trust and self.credential_source is not None:
                if await self._provider_trust_reason(catalog, trust_list, vc_cache) is not None:
                    attempted += 1
                    if attempted >= top_k:
                        break
                    print("[pythia] trying next candidate...")
                    continue

            if verify_trust:
                if await self._offer_trust_reason(offer, asset, catalog) is not None:
                    attempted += 1
                    if attempted >= top_k:
                        break
                    print("[pythia] trying next candidate...")
                    continue

            try:
                agreement_id = await self.ds.negotiate(
                    provider_dsp=catalog.provider_dsp,
                    provider_id=catalog.provider_id,
                    offer_id=offer.id,
                    asset_id=asset.id,
                    timeout=timeout,
                )
                data = await self.ds.fetch(
                    provider_dsp=catalog.provider_dsp,
                    provider_id=catalog.provider_id,
                    agreement_id=agreement_id,
                    asset_id=asset.id,
                    timeout=timeout,
                )
                print(f"[pythia] fetched {len(data)} bytes from {catalog.provider_id}")
                return data

            except Exception as e:
                attempted += 1
                print(f"[pythia] failed to negotiate/fetch {asset.id!r}: {e}")
                if attempted >= top_k:
                    break
                print("[pythia] trying next candidate...")

        print("[pythia] ask() exhausted candidates, no data retrieved")
        return None

    # ── Render path: return a readable Answer (default) ─────────────────────────

    async def _ask_render(
        self,
        query: str,
        ranked: list[RankedAsset],
        top_k: int,
        min_score: float,
        timeout: float,
        verify_trust: bool,
        trust_list: set[str] | None,
        synthesizer: Synthesizer | None,  # type: ignore[name-defined]
    ) -> bytes | Answer | None:  # type: ignore[name-defined]
        from .synthesize import Answer, FetchedAsset, LLMSynthesizer

        fetched: list[FetchedAsset] = []
        vc_cache: dict[str, str | None] = {}
        reasons: list[str] = []

        for candidate in ranked[:top_k]:
            if candidate.score < min_score:
                print(f"[pythia] score {candidate.score:.3f} below threshold, stopping")
                reasons.append(
                    f"the closest match scored {candidate.score:.2f}, below the "
                    f"{min_score} relevance threshold"
                )
                break

            asset = candidate.asset
            catalog = candidate.catalog
            offer = asset.offers[0]

            print(
                f"[pythia] fetching {asset.title or asset.id!r} "
                f"(score={candidate.score:.3f}, provider={catalog.provider_id})"
            )

            if verify_trust and self.credential_source is not None:
                reason = await self._provider_trust_reason(catalog, trust_list, vc_cache)
                if reason is not None:
                    reasons.append(f"provider {catalog.provider_id} was refused — {reason}")
                    continue

            if verify_trust:
                reason = await self._offer_trust_reason(offer, asset, catalog)
                if reason is not None:
                    reasons.append(f"offer from {catalog.provider_id} was rejected — {reason}")
                    continue

            try:
                agreement_id = await self.ds.negotiate(
                    provider_dsp=catalog.provider_dsp,
                    provider_id=catalog.provider_id,
                    offer_id=offer.id,
                    asset_id=asset.id,
                    timeout=timeout,
                )
                data = await self.ds.fetch(
                    provider_dsp=catalog.provider_dsp,
                    provider_id=catalog.provider_id,
                    agreement_id=agreement_id,
                    asset_id=asset.id,
                    timeout=timeout,
                )
                print(f"[pythia] fetched {len(data)} bytes from {catalog.provider_id}")
                fetched.append(
                    FetchedAsset(
                        asset_id=asset.id,
                        provider_id=catalog.provider_id,
                        title=asset.title,
                        data=data,
                    )
                )
            except Exception as e:
                print(f"[pythia] failed to fetch {asset.id!r}: {e}")
                reasons.append(
                    f"could not retrieve {asset.title or asset.id!r} from "
                    f"{catalog.provider_id}: {e}"
                )

        if not fetched:
            note = " ".join(reasons) if reasons else "No matching data was found."
            print(f"[pythia] no data retrieved — {note}")
            return Answer(query=query, table=[], sources=[], note=note)

        # Binary auto-fallback: only text payloads can be tabulated. If every match
        # is binary, hand back the best match's raw bytes rather than feeding a
        # blob to the synthesizer.
        text_sources: list[FetchedAsset] = []
        for fa in fetched:
            try:
                fa.data.decode("utf-8")
                text_sources.append(fa)
            except UnicodeDecodeError:
                print(f"[pythia] {fa.asset_id!r} is binary ({len(fa.data)} bytes) — not tabular")
        if not text_sources:
            print("[pythia] all matches are binary — returning raw bytes (not tabular)")
            return fetched[0].data

        synth = synthesizer or LLMSynthesizer()
        print(f"[pythia] synthesizing answer from {len(text_sources)} source(s)")
        return await synth.synthesize(query, text_sources)
