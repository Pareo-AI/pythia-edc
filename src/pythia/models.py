"""Pydantic models for Eclipse EDC Management API v3 responses."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# ── JSON-LD context helpers ────────────────────────────────────────────────────

EDC_NAMESPACE = "https://w3id.org/edc/v0.0.1/ns/"
EDC_CONTEXT = {"@vocab": EDC_NAMESPACE}
PROTOCOL = "dataspace-protocol-http:2025-1"


def _edc(key: str) -> str:
    """Qualify a key with the EDC namespace for JSON-LD parsing."""
    return f"{EDC_NAMESPACE}{key}"


# ── Catalog models ─────────────────────────────────────────────────────────────

class PolicyOffer(BaseModel):
    """An ODRL offer extracted from a catalog dataset."""
    id: str = Field(alias="@id")
    assigner: str | None = None
    target: str | None = None
    raw: dict = Field(default_factory=dict, exclude=True)

    model_config = {"populate_by_name": True}


class CatalogAsset(BaseModel):
    """A single asset/dataset from an EDC catalog."""
    id: str = Field(alias="@id")
    title: str | None = None
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    content_type: str | None = None
    offers: list[PolicyOffer] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @classmethod
    def from_dcat(cls, dataset: dict) -> CatalogAsset:
        """Parse a DCAT dataset entry from an EDC catalog response."""
        offers: list[PolicyOffer] = []
        raw_offers = dataset.get("hasPolicy", [])
        if isinstance(raw_offers, dict):
            raw_offers = [raw_offers]
        for offer in raw_offers:
            offers.append(PolicyOffer(
                **{"@id": offer.get("@id", "")},
                assigner=offer.get("assigner") or offer.get(f"{EDC_NAMESPACE}assigner"),
                target=(
                    offer.get("target")
                    or offer.get(f"{EDC_NAMESPACE}target")
                    or dataset.get("@id")
                ),
                raw=offer,
            ))

        # Extract DCT fields (may be plain, EDC-prefixed, or DCT-namespaced)
        # EDC uses edc:name for the asset name in DCAT responses
        title = (
            dataset.get("title")
            or dataset.get("name")
            or dataset.get("edc:name")
            or dataset.get(f"{EDC_NAMESPACE}name")
            or dataset.get("dct:title")
            or dataset.get("http://purl.org/dc/terms/title")
        )
        description = (
            dataset.get("description")
            or dataset.get("edc:description")
            or dataset.get(f"{EDC_NAMESPACE}description")
            or dataset.get("dct:description")
            or dataset.get("http://purl.org/dc/terms/description")
        )
        keywords_raw = (
            dataset.get("keyword")
            or dataset.get("dcat:keyword")
            or []
        )
        if isinstance(keywords_raw, str):
            keywords_raw = [keywords_raw]
        content_type = (
            dataset.get("mediaType")
            or dataset.get("dcat:mediaType")
        )

        return cls(
            **{"@id": dataset.get("@id", "")},
            title=title,
            description=description,
            keywords=keywords_raw,
            content_type=content_type,
            offers=offers,
        )


class Catalog(BaseModel):
    """Parsed EDC catalog from a single provider."""
    provider_dsp: str
    provider_id: str
    assets: list[CatalogAsset] = Field(default_factory=list)

    @property
    def first_offer(self) -> tuple[CatalogAsset, PolicyOffer] | None:
        """Return (asset, offer) for first asset with any offer."""
        for asset in self.assets:
            if asset.offers:
                return asset, asset.offers[0]
        return None


# ── Negotiation models ─────────────────────────────────────────────────────────

class NegotiationState(BaseModel):
    """State of a contract negotiation."""
    id: str = Field(alias="@id")
    state: str
    contract_agreement_id: str | None = None

    model_config = {"populate_by_name": True}

    @field_validator("state", mode="before")
    @classmethod
    def strip_namespace(cls, v: str) -> str:
        """Strip EDC namespace prefix if present."""
        return v.replace(EDC_NAMESPACE, "").upper()

    @property
    def is_terminal(self) -> bool:
        return self.state in ("FINALIZED", "TERMINATED", "ERROR")

    @property
    def is_finalized(self) -> bool:
        return self.state == "FINALIZED"

    @property
    def is_failed(self) -> bool:
        return self.state in ("TERMINATED", "ERROR")


# ── Transfer models ────────────────────────────────────────────────────────────

class TransferState(BaseModel):
    """State of a transfer process."""
    id: str = Field(alias="@id")
    state: str

    model_config = {"populate_by_name": True}

    @field_validator("state", mode="before")
    @classmethod
    def strip_namespace(cls, v: str) -> str:
        return v.replace(EDC_NAMESPACE, "").upper()

    @property
    def is_started(self) -> bool:
        return self.state == "STARTED"

    @property
    def is_failed(self) -> bool:
        return self.state in ("TERMINATED", "ERROR")


# ── EDR token ─────────────────────────────────────────────────────────────────

class EDRToken(BaseModel):
    """Endpoint Data Reference — token to retrieve data from provider."""
    endpoint: str
    authorization: str
    auth_type: str = "bearer"
    endpoint_type: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": self.authorization}
