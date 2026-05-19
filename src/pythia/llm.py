"""Local LLM client (Ollama) — shared renderer for Explainer and Synthesizer.

The model is a renderer only: it never decides trust and never invents data.
Runs against a local Ollama daemon so no query, data, or error leaves the machine
(preserves Pythia's offline-privacy property).
"""

from __future__ import annotations

import httpx

from .config import TLSConfig

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:e4b"


class OllamaClient:
    """Minimal async client for the Ollama generate API."""

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
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0},
        }
        if system is not None:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=self.timeout, **self._tls.httpx_kwargs()) as client:
            resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json()["response"].strip()
