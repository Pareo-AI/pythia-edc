"""Internal httpx session with EDC auth and error handling."""

from __future__ import annotations

import json

import httpx

from .config import DEFAULT_MAX_RESPONSE_BYTES, TLSConfig
from .errors import ConnectorError


class EDCClient:
    """
    Thin async httpx wrapper for EDC Management API.

    Handles:
    - Auth header injection
    - Content-Type / Accept headers
    - HTTP error -> ConnectorError mapping
    - A hard cap on the response body size (memory-DoS guard): responses are
      streamed and aborted past ``max_response_bytes``. Catalog payloads are
      proxied provider data and thus attacker-influenced, so the management
      plane is capped too, not just the data-plane fetch.
    """

    def __init__(
        self,
        management_url: str,
        api_key: str = "password",
        api_key_header: str = "X-Api-Key",
        timeout: float = 30.0,
        tls: TLSConfig | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        # Normalise: strip trailing slash
        self.base_url = management_url.rstrip("/")
        self.max_response_bytes = max_response_bytes
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
        return await self._request("POST", path, json=body)

    async def get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        async with self._client.stream(method, url, json=json) as resp:
            raw = await self._read_capped(resp, url)
            return self._parse(resp, raw, url)

    async def _read_capped(self, resp: httpx.Response, url: str) -> bytes:
        """Stream the body, aborting if it exceeds ``max_response_bytes``."""
        declared = resp.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > self.max_response_bytes:
                    raise ConnectorError(
                        f"EDC response from {url} declares {declared} bytes, over the "
                        f"{self.max_response_bytes}-byte cap",
                        status_code=resp.status_code,
                    )
            except ValueError:
                pass  # unparseable header — fall through to the streamed check

        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise ConnectorError(
                    f"EDC response from {url} exceeded the {self.max_response_bytes}-byte cap",
                    status_code=resp.status_code,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _parse(self, resp: httpx.Response, raw: bytes, url: str) -> dict:
        if resp.status_code >= 400:
            body = raw[:500].decode("utf-8", errors="replace")
            raise ConnectorError(
                f"EDC returned HTTP {resp.status_code} for {url}",
                status_code=resp.status_code,
                body=body,
            )
        try:
            return json.loads(raw)
        except ValueError as exc:
            snippet = raw[:200].decode("utf-8", errors="replace")
            raise ConnectorError(
                f"EDC returned non-JSON response from {url}: {snippet}"
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> EDCClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
