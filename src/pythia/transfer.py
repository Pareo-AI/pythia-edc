"""
Transfer process initiation and EDR token retrieval.

Flow:
    1. POST /management/v3/transferprocesses  → transfer_id
    2. Poll until state == STARTED
    3. GET  /management/v3/edrs/{transfer_id}/dataaddress  → EDRToken
    4. GET  {endpoint}  Authorization: {token}  → data
"""

from __future__ import annotations

import asyncio

import httpx

from ._http import EDCClient
from .config import TLSConfig
from .errors import EDRError, TransferError, TransferTimeout
from .models import EDC_CONTEXT, PROTOCOL, EDRToken, TransferState


class TransferController:
    def __init__(
        self,
        client: EDCClient,
        api_version: str = "v3",
        tls: TLSConfig | None = None,
    ) -> None:
        self._c = client
        self._v = api_version
        self._tls = tls or TLSConfig()

    async def start(
        self,
        provider_dsp: str,
        provider_id: str,
        contract_id: str,
        asset_id: str,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> str:
        """
        Initiate HttpData-PULL transfer.

        Returns:
            transfer_id (str) — use with edr() to get access token
        """
        body = {
            "@context": EDC_CONTEXT,
            "@type": "TransferRequest",
            "counterPartyAddress": provider_dsp,
            "connectorId": provider_id,
            "contractId": contract_id,
            "assetId": asset_id,
            "protocol": PROTOCOL,
            "dataDestination": {"type": "HttpProxy"},
            "transferType": "HttpData-PULL",
        }

        resp = await self._c.post(f"/{self._v}/transferprocesses", body)
        transfer_id = resp.get("@id")
        if not transfer_id:
            raise TransferError(f"No @id in transfer response: {resp}")

        # Poll until STARTED (EDR available)
        elapsed = 0.0
        while elapsed < timeout:
            state = await self._poll(transfer_id)

            if state.is_started:
                return transfer_id

            if state.is_failed:
                raise TransferError(
                    f"Transfer {transfer_id} reached {state.state}",
                    transfer_id=transfer_id,
                    state=state.state,
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TransferTimeout(
            f"Transfer {transfer_id} did not reach STARTED within {timeout}s",
            transfer_id=transfer_id,
        )

    async def _poll(self, transfer_id: str) -> TransferState:
        data = await self._c.get(f"/{self._v}/transferprocesses/{transfer_id}")
        state_str = data.get("state") or "UNKNOWN"
        return TransferState(**{"@id": data.get("@id", transfer_id)}, state=state_str)

    async def edr(self, transfer_id: str) -> EDRToken:
        """
        Retrieve EDR token after transfer reaches STARTED state.

        No polling — call after start() which already waits for STARTED.

        Returns:
            EDRToken with endpoint and authorization fields
        """
        try:
            data = await self._c.get(
                f"/{self._v}/edrs/{transfer_id}/dataaddress"
            )
        except Exception as exc:
            raise EDRError(f"Failed to retrieve EDR for transfer {transfer_id}: {exc}") from exc

        endpoint = data.get("endpoint") or data.get("https://w3id.org/edc/v0.0.1/ns/endpoint")
        authorization = (
            data.get("authorization")
            or data.get("https://w3id.org/edc/v0.0.1/ns/authorization")
        )

        if not endpoint or not authorization:
            # Don't interpolate the raw EDR into the message — it carries the
            # access token. Report only which field(s) were missing.
            missing = [
                name
                for name, present in (("endpoint", endpoint), ("authorization", authorization))
                if not present
            ]
            raise EDRError(
                f"EDR response missing {', '.join(missing)} (keys: {sorted(data)})"
            )

        return EDRToken(
            endpoint=endpoint,
            authorization=authorization,
            auth_type=data.get("authType", "bearer"),
            endpoint_type=data.get("endpointType"),
        )

    async def fetch_data(self, edr: EDRToken, path: str = "") -> bytes:
        """
        Retrieve data from provider using EDR token.

        Args:
            edr:  EDR token from edr()
            path: Optional path suffix after the endpoint URL

        Returns:
            Raw response bytes
        """
        url = edr.endpoint.rstrip("/")
        if path:
            url = f"{url}/{path.lstrip('/')}"

        async with httpx.AsyncClient(timeout=30.0, **self._tls.httpx_kwargs()) as client:
            resp = await client.get(url, headers=edr.headers)
            resp.raise_for_status()
            return resp.content
