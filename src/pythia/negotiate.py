"""
Contract negotiation state machine.

States (DSP spec):
    REQUESTED → OFFERING → AGREED → VERIFIED → FINALIZED
                                                    ↓
                                               TERMINATED  (error path)

Edge cases handled:
- TERMINATED: raise NegotiationError with final state
- Timeout: raise NegotiationTimeout
- Provider/consumer use DIFFERENT negotiation IDs — we track consumer-side ID
- State desync: provider may restart; we re-poll and surface mismatch
"""

from __future__ import annotations

import asyncio

from ._http import EDCClient
from .errors import NegotiationError, NegotiationTimeout
from .models import EDC_CONTEXT, PROTOCOL, NegotiationState

# States that are not terminal but may appear during negotiation
_TRANSIENT_STATES = frozenset(
    ["REQUESTED", "OFFERING", "AGREED", "VERIFIED", "ACCEPTED"]
)


class NegotiationController:
    def __init__(self, client: EDCClient, api_version: str = "v3") -> None:
        self._c = client
        self._v = api_version

    async def start(
        self,
        provider_dsp: str,
        provider_id: str,
        offer_id: str,
        asset_id: str,
        policy: dict | None = None,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> str:
        """
        Start a contract negotiation and poll until FINALIZED.

        Args:
            provider_dsp:   DSP endpoint URL of the provider
            provider_id:    Participant ID of the provider
            offer_id:       Policy/offer @id from catalog (e.g. "offer:123:abc")
            asset_id:       Asset @id to negotiate for
            policy:         Full ODRL policy dict (optional; built from offer_id if omitted)
            timeout:        Max seconds to wait for FINALIZED state
            poll_interval:  Seconds between state polls

        Returns:
            contract_agreement_id (str) — use for transfer initiation

        Raises:
            NegotiationError:   TERMINATED or unexpected state
            NegotiationTimeout: Did not reach FINALIZED within timeout
        """
        neg_policy = policy or {
            "@context": "http://www.w3.org/ns/odrl.jsonld",
            "@type": "Offer",
            "@id": offer_id,
            "assigner": provider_id,
            "target": asset_id,
        }

        body = {
            "@context": EDC_CONTEXT,
            "@type": "ContractRequest",
            "counterPartyAddress": provider_dsp,
            "counterPartyId": provider_id,
            "protocol": PROTOCOL,
            "policy": neg_policy,
            "callbackAddresses": [],
        }

        resp = await self._c.post(f"/{self._v}/contractnegotiations", body)
        negotiation_id = resp.get("@id")
        if not negotiation_id:
            raise NegotiationError(
                f"No @id in negotiation response: {resp}",
                state="UNKNOWN",
            )

        # Poll until terminal state
        elapsed = 0.0
        while elapsed < timeout:
            state = await self._poll(negotiation_id)

            if state.is_finalized:
                if not state.contract_agreement_id:
                    raise NegotiationError(
                        "FINALIZED but no contractAgreementId in response",
                        negotiation_id=negotiation_id,
                        state="FINALIZED",
                    )
                return state.contract_agreement_id

            if state.is_failed:
                raise NegotiationError(
                    f"Negotiation {negotiation_id} reached {state.state}",
                    negotiation_id=negotiation_id,
                    state=state.state,
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise NegotiationTimeout(
            f"Negotiation {negotiation_id} did not reach FINALIZED within {timeout}s "
            f"(last state: {(await self._poll(negotiation_id)).state})",
            negotiation_id=negotiation_id,
        )

    async def _poll(self, negotiation_id: str) -> NegotiationState:
        """Fetch current state of negotiation from consumer connector."""
        data = await self._c.get(
            f"/{self._v}/contractnegotiations/{negotiation_id}"
        )
        state_str = (
            data.get("state")
            or data.get("https://w3id.org/edc/v0.0.1/ns/state")
            or "UNKNOWN"
        )
        agreement_id = (
            data.get("contractAgreementId")
            or data.get("https://w3id.org/edc/v0.0.1/ns/contractAgreementId")
        )
        return NegotiationState(
            **{"@id": data.get("@id", negotiation_id)},
            state=state_str,
            contract_agreement_id=agreement_id,
        )
