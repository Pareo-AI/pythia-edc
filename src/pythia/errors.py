"""Typed exceptions for Pythia EDC client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrustFailure:
    """One structured SHACL validation result — the deterministic source of truth.

    An Explainer renders these into prose; it never produces the verdict itself.
    """

    message: str | None = None
    focus_node: str | None = None
    result_path: str | None = None
    value: str | None = None
    constraint: str | None = None
    severity: str | None = None


class PythiaError(Exception):
    """Base exception for all Pythia errors."""


class NegotiationError(PythiaError):
    """Raised when contract negotiation fails or terminates."""

    def __init__(self, message: str, negotiation_id: str | None = None, state: str | None = None):
        super().__init__(message)
        self.negotiation_id = negotiation_id
        self.state = state


class NegotiationTimeout(NegotiationError):
    """Raised when negotiation does not reach FINALIZED within timeout."""


class TransferError(PythiaError):
    """Raised when data transfer fails."""

    def __init__(self, message: str, transfer_id: str | None = None, state: str | None = None):
        super().__init__(message)
        self.transfer_id = transfer_id
        self.state = state


class TransferTimeout(TransferError):
    """Raised when transfer does not reach STARTED within timeout."""


class EDRError(PythiaError):
    """Raised when EDR token cannot be retrieved."""


class CatalogError(PythiaError):
    """Raised when catalog query fails."""


class ConnectorError(PythiaError):
    """Raised when connector is unreachable or returns unexpected response."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TrustError(PythiaError):
    """Raised when a provider's ODRL offer fails SHACL validation."""

    def __init__(self, message: str, failures: list[TrustFailure] | None = None):
        super().__init__(message)
        self.failures: list[TrustFailure] = failures or []


class CredentialError(PythiaError):
    """Raised when a Verifiable Credential fails structural or validity checks."""

    def __init__(self, message: str, failures: list[TrustFailure] | None = None):
        super().__init__(message)
        self.failures: list[TrustFailure] = failures or []
