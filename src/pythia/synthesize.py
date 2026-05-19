"""Synthesizer slice — fetch top-k assets and synthesize a tabular answer.

The LLM is a renderer over provided data. It extracts and aggregates values
that are present in the source payloads and must never invent rows or numbers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .llm import OllamaClient

_DEFAULT_SYNTH_MODEL = "gemma4:e4b"

_SYSTEM_PROMPT = (
    "You are a data extractor. You are given a user query and one or more data payloads "
    "retrieved from a Gaia-X data space. "
    "Your task: extract values from the provided payloads and return them as a JSON array "
    "of flat row objects that answer the query. "
    "RULES — you must follow all of them:\n"
    "1. Only use values that appear verbatim in the provided data. Never invent, estimate, "
    "or interpolate numbers.\n"
    "2. Do not add commentary, markdown, or explanation — output ONLY a valid JSON array.\n"
    "3. If the data contains no relevant values, return an empty array: []\n"
    "4. Keep each row flat (no nested objects).\n"
    "5. Use consistent key names across rows."
)


@dataclass(frozen=True)
class FetchedAsset:
    asset_id: str
    provider_id: str
    title: str | None
    data: bytes


@dataclass
class Answer:
    query: str
    table: list[dict]
    sources: list[dict]
    note: str | None = None

    def to_markdown(self) -> str:
        lines: list[str] = []

        if not self.table:
            lines.append("*No results found.*")
        else:
            headers = list(self.table[0].keys())
            lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in self.table:
                lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")

        if self.note:
            lines.append(f"\n> **Note:** {self.note}")

        if self.sources:
            lines.append("\n**Sources:**")
            for src in self.sources:
                title = src.get("title") or "(untitled)"
                lines.append(
                    f"- `{src.get('asset_id', '?')}` from `{src.get('provider_id', '?')}` — {title}"
                )

        return "\n".join(lines)


@runtime_checkable
class Synthesizer(Protocol):
    async def synthesize(self, query: str, sources: list[FetchedAsset]) -> Answer: ...


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text


class LLMSynthesizer:
    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient(model=_DEFAULT_SYNTH_MODEL)

    async def synthesize(self, query: str, sources: list[FetchedAsset]) -> Answer:
        provenance = [
            {
                "asset_id": s.asset_id,
                "provider_id": s.provider_id,
                "title": s.title,
            }
            for s in sources
        ]

        payload_parts: list[str] = []
        for s in sources:
            title_label = s.title or s.asset_id
            try:
                text = s.data.decode("utf-8")
            except UnicodeDecodeError:
                text = s.data.decode("latin-1", errors="replace")
            payload_parts.append(f"[Source: {title_label}]\n{text}")

        prompt = (
            f"Query: {query}\n\n"
            "Data payloads:\n"
            + "\n\n".join(payload_parts)
            + "\n\nReturn a JSON array of rows answering the query:"
        )

        try:
            raw = await self._client.generate(prompt, system=_SYSTEM_PROMPT)
        except Exception as exc:
            return Answer(
                query=query,
                table=[],
                sources=provenance,
                note=f"Synthesis model unreachable: {exc}",
            )
        cleaned = _strip_fences(raw)

        try:
            rows = json.loads(cleaned)
            if not isinstance(rows, list):
                rows = [rows] if isinstance(rows, dict) else []
            return Answer(query=query, table=rows, sources=provenance)
        except json.JSONDecodeError:
            return Answer(
                query=query,
                table=[],
                sources=provenance,
                note=f"JSON parse failed; raw model output: {raw[:200]}",
            )
