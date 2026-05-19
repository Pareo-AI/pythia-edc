"""Internal httpx session with EDC auth and error handling."""

from __future__ import annotations

import httpx

from .config import TLSConfig
from .errors import ConnectorError


class EDCClient:
    """
    Thin async httpx wrapper for EDC Management API.

    Handles:
    - Auth header injection
    - Content-Type / Accept headers
    - HTTP error -> ConnectorError mapping
    """

    def __init__(
        self,
        management_url: str,
        api_key: str = "password",
        api_key_header: str = "X-Api-Key",
        timeout: float = 30.0,
        tls: TLSConfig | None = None,
    ) -> None:
        # Normalise: strip trailing slash
        self.base_url = management_url.rstrip("/")
        tls = tls or TLSConfig()
        self._headers = {
            api_key_header: api_key,
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=timeout,
            **tls.httpx_kwargs(),
        )

    async def post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        resp = await self._client.post(url, json=body)
        return self._parse(resp)

    async def get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        resp = await self._client.get(url)
        return self._parse(resp)

    def _parse(self, resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            body = ""
            try:
                body = resp.text[:500]
            except Exception:
                pass
            raise ConnectorError(
                f"EDC returned HTTP {resp.status_code} for {resp.url}",
                status_code=resp.status_code,
                body=body,
            )
        try:
            return resp.json()
        except Exception as exc:
            raise ConnectorError(
                f"EDC returned non-JSON response from {resp.url}: {resp.text[:200]}"
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> EDCClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
