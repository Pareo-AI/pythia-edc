"""Connection and TLS configuration for outbound httpx clients."""

from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass, field

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


@dataclass(frozen=True)
class TLSConfig:
    verify: bool = True
    ca_bundle: str | None = None
    client_cert: str | None = None
    client_key: str | None = None

    def httpx_kwargs(self) -> dict:
        """Kwargs to splat into httpx.AsyncClient(...) / httpx.Client(...).

        With no CA bundle or client cert, pass a plain bool ``verify``. When a CA
        bundle and/or client cert (mTLS) is configured, build an explicit
        ``ssl.SSLContext``: httpx's ``verify=<path>`` and ``cert=<tuple>`` forms
        are deprecated and, used together, fail to present the client cert for
        mTLS — so we construct the context ourselves.
        """
        if not self.ca_bundle and not self.client_cert:
            return {"verify": self.verify}

        if self.verify:
            ctx = ssl.create_default_context(cafile=self.ca_bundle)  # cafile=None → system trust
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if self.client_cert:
            ctx.load_cert_chain(certfile=self.client_cert, keyfile=self.client_key)
        return {"verify": ctx}

    @classmethod
    def from_env(cls, prefix: str = "PYTHIA_") -> TLSConfig:
        return cls(
            verify=_env_bool(os.environ.get(f"{prefix}VERIFY_SSL"), True),
            ca_bundle=os.environ.get(f"{prefix}CA_BUNDLE") or None,
            client_cert=os.environ.get(f"{prefix}CLIENT_CERT") or None,
            client_key=os.environ.get(f"{prefix}CLIENT_KEY") or None,
        )


@dataclass(frozen=True)
class ConnectorConfig:
    management_url: str
    api_key: str = "password"
    api_key_header: str = "X-Api-Key"
    api_version: str = "v3"
    providers: list[dict] = field(default_factory=list)
    timeout: float = 30.0
    tls: TLSConfig = field(default_factory=TLSConfig)

    @classmethod
    def from_env(cls, prefix: str = "PYTHIA_") -> ConnectorConfig:
        return cls(
            management_url=os.environ.get(
                f"{prefix}MANAGEMENT_URL", "http://localhost:29193/management"
            ),
            api_key=os.environ.get(f"{prefix}API_KEY", "password"),
            api_key_header=os.environ.get(f"{prefix}API_KEY_HEADER", "X-Api-Key"),
            api_version=os.environ.get(f"{prefix}API_VERSION", "v3"),
            providers=json.loads(os.environ.get(f"{prefix}PROVIDERS", "[]")),
            timeout=float(os.environ.get(f"{prefix}TIMEOUT", "30.0")),
            tls=TLSConfig.from_env(prefix),
        )
