"""
Pythia — Ask Your Gaia-X Data Space.

    from pythia import DataSpace

    ds = DataSpace(
        management_url="http://localhost:29193/management",
        api_key="password",
    )

    # Low-level: full control
    catalog = await ds.catalog.query(
        provider_dsp="http://localhost:19194/protocol/2025-1",
        provider_id="provider",
    )
    asset, offer = catalog.first_offer
    agreement_id = await ds.negotiate(
        provider_dsp="http://localhost:19194/protocol/2025-1",
        provider_id="provider",
        offer_id=offer.id,
        asset_id=asset.id,
    )
    data = await ds.fetch(agreement_id=agreement_id, asset_id=asset.id,
                          provider_dsp="http://localhost:19194/protocol/2025-1",
                          provider_id="provider")

    # High-level: natural language (requires pythia-edc[ask])
    data = await ds.ask("CO2 emissions from German automotive suppliers 2023")
"""

from __future__ import annotations

from ._http import EDCClient
from .ask import DEFAULT_MIN_SCORE
from .catalog import CatalogController
from .config import ConnectorConfig, TLSConfig
from .credential_source import CredentialSource, StaticCredentialSource
from .errors import (
    CatalogError,
    ConnectorError,
    CredentialError,
    EDRError,
    NegotiationError,
    NegotiationTimeout,
    PythiaError,
    TransferError,
    TransferTimeout,
    TrustError,
    TrustFailure,
)
from .explain import Explainer, LLMExplainer, TemplateExplainer
from .models import Catalog, CatalogAsset, EDRToken, PolicyOffer
from .negotiate import NegotiationController
from .synthesize import Answer, FetchedAsset, LLMSynthesizer, Synthesizer
from .transfer import TransferController

__version__ = "0.2.0"
__all__ = [
    "DataSpace",
    "PythiaError",
    "NegotiationError",
    "NegotiationTimeout",
    "TransferError",
    "TransferTimeout",
    "EDRError",
    "CatalogError",
    "ConnectorError",
    "TrustError",
    "TrustFailure",
    "CredentialError",
    "CredentialSource",
    "StaticCredentialSource",
    "Explainer",
    "TemplateExplainer",
    "LLMExplainer",
    "Catalog",
    "CatalogAsset",
    "PolicyOffer",
    "EDRToken",
    "Answer",
    "FetchedAsset",
    "Synthesizer",
    "LLMSynthesizer",
    "TLSConfig",
    "ConnectorConfig",
]


class DataSpace:
    """
    Async client for a Gaia-X / Eclipse EDC data space.

    Args:
        management_url:   Base URL of the consumer connector Management API
                          (e.g. "http://localhost:29193/management")
        api_key:          Management API key (default "password" for local dev)
        api_key_header:   Header name for API key (default "X-Api-Key")
        api_version:      Management API version prefix (default "v3")
        providers:        Default provider list for ds.ask() fan-out
        verify_ssl:       Verify TLS certificates (default True; set False for dev)
        ca_bundle:        Path to a PEM CA bundle for verifying server certs
        client_cert:      Path to client cert PEM for mutual TLS (mTLS)
        client_key:       Path to client private key PEM for mutual TLS (mTLS)
        timeout:          Per-request timeout in seconds (default 30.0)
        tls:              Explicit TLSConfig; overrides the flat verify_ssl/ca_bundle/
                          client_cert/client_key kwargs when provided.
    """

    def __init__(
        self,
        management_url: str,
        api_key: str = "password",
        api_key_header: str = "X-Api-Key",
        api_version: str = "v3",
        providers: list[dict] | None = None,
        verify_ssl: bool = True,
        ca_bundle: str | None = None,
        client_cert: str | None = None,
        client_key: str | None = None,
        timeout: float = 30.0,
        tls: TLSConfig | None = None,
    ) -> None:
        if tls is None:
            tls = TLSConfig(
                verify=verify_ssl,
                ca_bundle=ca_bundle,
                client_cert=client_cert,
                client_key=client_key,
            )
        self._http = EDCClient(
            management_url=management_url,
            api_key=api_key,
            api_key_header=api_key_header,
            timeout=timeout,
            tls=tls,
        )
        self._v = api_version
        self._providers = providers or []

        # Sub-controllers
        self.catalog = CatalogController(self._http, api_version)
        self._negotiate = NegotiationController(self._http, api_version)
        self._transfer = TransferController(self._http, api_version, tls=tls)

    @classmethod
    def from_env(cls, prefix: str = "PYTHIA_") -> DataSpace:
        """Build a DataSpace from environment via ConnectorConfig.from_env()."""
        cfg = ConnectorConfig.from_env(prefix)
        return cls(
            management_url=cfg.management_url,
            api_key=cfg.api_key,
            api_key_header=cfg.api_key_header,
            api_version=cfg.api_version,
            providers=cfg.providers,
            timeout=cfg.timeout,
            tls=cfg.tls,
        )

    # ── Core flow ──────────────────────────────────────────────────────────────

    async def negotiate(
        self,
        provider_dsp: str,
        provider_id: str,
        offer_id: str,
        asset_id: str,
        policy: dict | None = None,
        timeout: float = 30.0,
    ) -> str:
        """
        Negotiate a contract and return the contract agreement ID.

        Handles full state machine including TERMINATED error path.
        Raises NegotiationError on TERMINATED; NegotiationTimeout on timeout.
        """
        return await self._negotiate.start(
            provider_dsp=provider_dsp,
            provider_id=provider_id,
            offer_id=offer_id,
            asset_id=asset_id,
            policy=policy,
            timeout=timeout,
        )

    async def transfer(
        self,
        provider_dsp: str,
        provider_id: str,
        agreement_id: str,
        asset_id: str,
        timeout: float = 30.0,
    ) -> EDRToken:
        """
        Initiate transfer and return EDR access token when ready.

        Raises TransferError on failure; TransferTimeout on timeout.
        """
        transfer_id = await self._transfer.start(
            provider_dsp=provider_dsp,
            provider_id=provider_id,
            contract_id=agreement_id,
            asset_id=asset_id,
            timeout=timeout,
        )
        return await self._transfer.edr(transfer_id)

    async def fetch(
        self,
        provider_dsp: str,
        provider_id: str,
        agreement_id: str,
        asset_id: str,
        path: str = "",
        timeout: float = 30.0,
    ) -> bytes:
        """
        High-level: transfer + retrieve data in one call.

        Returns raw response bytes. Use with json.loads() or pandas.read_csv() etc.
        """
        edr = await self.transfer(
            provider_dsp=provider_dsp,
            provider_id=provider_id,
            agreement_id=agreement_id,
            asset_id=asset_id,
            timeout=timeout,
        )
        return await self._transfer.fetch_data(edr, path=path)

    # ── Natural language interface (Layer 2) ───────────────────────────────────

    async def ask(
        self,
        query: str,
        providers: list[dict] | None = None,
        top_k: int = 1,
        min_score: float = DEFAULT_MIN_SCORE,
        timeout: float = 30.0,
        verify_trust: bool = False,
        explainer: Explainer | None = None,
        credential_source: CredentialSource | None = None,
        trust_list: set[str] | None = None,
        raw: bool = False,
        synthesizer: Synthesizer | None = None,
    ) -> bytes | Answer | None:
        """
        Ask your data space a natural language question.

        Fans out catalog queries to all registered providers, ranks assets
        by semantic similarity to query, negotiates and retrieves best match.

        Requires: pip install pythia-edc[ask]

        Args:
            query:        Natural language query (e.g. "CO2 data for German suppliers")
            providers:    List of {"dsp": "...", "id": "..."} dicts.
                          Defaults to DataSpace(providers=[...]) if set at init.
            top_k:        Number of top matches to try (negotiates first, falls back)
            min_score:    Minimum cosine similarity score to attempt negotiation
            timeout:      Negotiation + transfer timeout per provider
            verify_trust: Validate each offer against a SHACL shape before negotiating.
                          Requires: pip install 'pythia-edc[trust]'
            explainer:    Optional Explainer to render a trust rejection into prose.
                          Defaults to a raw report. See pythia.explain.
            credential_source: Optional CredentialSource. When set and verify_trust=True,
                          each provider's Verifiable Credential is resolved and verified
                          (provider-level trust) before its offer is validated.
            trust_list:   Optional set of allowed issuer DIDs for provider-VC
                          verification. When None, the issuer-identity gate is skipped.
            raw:          Return the best-matching asset's raw bytes instead of a
                          readable Answer. Default False (synthesize a table; carry
                          any refusal reason in Answer.note). Non-tabular/binary
                          assets fall back to raw bytes even when raw=False.
            synthesizer:  Optional Synthesizer; defaults to LLMSynthesizer().

        Returns:
            By default an Answer (synthesized table + sources on success, or an
            empty table with an explanatory note on a refusal/miss). With raw=True,
            the best-matching asset's bytes, or None if no match found.
        """
        try:
            from .ask import AskController
        except ImportError as exc:
            raise ImportError(
                "ds.ask() requires sentence-transformers: "
                "pip install 'pythia-edc[ask]'"
            ) from exc

        providers_to_query = providers or self._providers
        if not providers_to_query:
            raise ValueError(
                "No providers configured. Pass providers=[...] to DataSpace() or ds.ask()."
            )

        ask_ctrl = AskController(
            self,
            explainer=explainer,
            synthesizer=synthesizer,
            credential_source=credential_source,
        )
        return await ask_ctrl.ask(
            query=query,
            providers=providers_to_query,
            top_k=top_k,
            min_score=min_score,
            timeout=timeout,
            verify_trust=verify_trust,
            trust_list=trust_list,
            raw=raw,
            synthesizer=synthesizer,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> DataSpace:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
