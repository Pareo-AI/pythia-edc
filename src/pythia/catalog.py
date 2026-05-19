"""EDC catalog query — POST /management/v3/catalog/request."""

from __future__ import annotations

from ._http import EDCClient
from .errors import CatalogError
from .models import EDC_CONTEXT, PROTOCOL, Catalog, CatalogAsset


class CatalogController:
    def __init__(self, client: EDCClient, api_version: str = "v3") -> None:
        self._c = client
        self._v = api_version

    async def query(
        self,
        provider_dsp: str,
        provider_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Catalog:
        """
        Fetch catalog from a single provider.

        Args:
            provider_dsp: DSP endpoint URL of the provider
                          (e.g. "http://localhost:19194/protocol")
            provider_id:  Participant ID of the provider (e.g. "provider")
            limit:        Max assets to return
            offset:       Pagination offset

        Returns:
            Catalog with parsed CatalogAsset list
        """
        body = {
            "@context": EDC_CONTEXT,
            "@type": "CatalogRequest",
            "counterPartyAddress": provider_dsp,
            "counterPartyId": provider_id,
            "protocol": PROTOCOL,
            "querySpec": {"offset": offset, "limit": limit},
        }

        try:
            data = await self._c.post(f"/{self._v}/catalog/request", body)
        except Exception as exc:
            raise CatalogError(f"Catalog query failed for {provider_id}: {exc}") from exc

        # DCAT response: datasets under "dataset" or "dcat:dataset"
        raw_datasets = data.get("dataset") or data.get("dcat:dataset") or []
        if isinstance(raw_datasets, dict):
            raw_datasets = [raw_datasets]

        assets = [CatalogAsset.from_dcat(ds) for ds in raw_datasets]

        return Catalog(
            provider_dsp=provider_dsp,
            provider_id=provider_id,
            assets=assets,
        )
