"""Local LLM client (LM Studio) — shared renderer for Explainer and Synthesizer.

The model is a renderer only: it never decides trust and never invents data.
Runs against a local LM Studio server (OpenAI-compatible API) so no query, data,
or error leaves the machine (preserves Pythia's offline-privacy property).

LM Studio exposes an OpenAI-compatible server at ``http://localhost:1234/v1``
(Developer tab → "Start Server"). Point Pythia elsewhere with the
``PYTHIA_LLM_BASE_URL`` / ``PYTHIA_LLM_MODEL`` environment variables, or by
passing ``base_url`` / ``model`` explicitly. The ``model`` must match the id LM
Studio reports at ``GET /v1/models`` for the currently loaded model.
"""

from __future__ import annotations

import os

import httpx

from .config import TLSConfig

# LM Studio's OpenAI-compatible server. The base URL includes the ``/v1`` prefix.
DEFAULT_BASE_URL = os.environ.get("PYTHIA_LLM_BASE_URL", "http://localhost:1234/v1")
DEFAULT_MODEL = os.environ.get("PYTHIA_LLM_MODEL", "google/gemma-4-e4b")


class LMStudioClient:
    """Minimal async client for LM Studio's OpenAI-compatible chat API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        tls: TLSConfig | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._tls = tls or TLSConfig()

    async def generate(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout, **self._tls.httpx_kwargs()) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
