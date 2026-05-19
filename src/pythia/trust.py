"""Gaia-X trust slice: SHACL validation of ODRL policy offers."""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING

from .errors import TrustError, TrustFailure

if TYPE_CHECKING:
    from rdflib import Graph, Node

_SHACL_NS = "http://www.w3.org/ns/shacl#"

_ODRL_CONTEXT = {
    "odrl": "http://www.w3.org/ns/odrl/2/",
    "target": "odrl:target",
    "assigner": "odrl:assigner",
    "assignee": "odrl:assignee",
    "permission": "odrl:permission",
    "prohibition": "odrl:prohibition",
    "obligation": "odrl:obligation",
}

_DEFAULT_SHAPE: str | None = None


def _default_shape_path() -> str:
    global _DEFAULT_SHAPE
    if _DEFAULT_SHAPE is None:
        pkg = resources.files("pythia") / "shapes" / "odrl_offer.ttl"
        _DEFAULT_SHAPE = str(pkg)
    return _DEFAULT_SHAPE


def validate_offer(offer: dict, shape_path: str | None = None, target: str | None = None) -> None:
    try:
        import pyshacl
        from rdflib import Graph
    except ImportError as exc:
        raise ImportError(
            "validate_offer() requires pyshacl and rdflib: "
            "pip install 'pythia-edc[trust]'"
        ) from exc

    doc = dict(offer)

    # Validate against a self-contained local context only. The offer comes from
    # the provider's catalog (attacker-controlled), so we MUST NOT let the JSON-LD
    # parser dereference remote string @contexts: doing so is an SSRF/DoS vector
    # and lets a hostile context remap ODRL terms to slip a non-conformant offer
    # past SHACL. Drop remote string contexts and supply _ODRL_CONTEXT ourselves;
    # inline dict contexts carry no network dependency, so keep them. Mirrors
    # credential.py:_shacl_validate.
    existing_ctx = doc.get("@context")
    inline_ctxs = [c for c in _as_list(existing_ctx) if isinstance(c, dict)]
    doc["@context"] = [*inline_ctxs, _ODRL_CONTEXT]

    doc["@type"] = "odrl:Offer"

    if "@id" not in doc:
        raise TrustError("ODRL offer is missing @id (must be a named IRI, not a blank node)")

    if target is not None and "target" not in doc:
        doc["target"] = target

    data_graph = Graph()
    data_graph.parse(data=json.dumps(doc), format="json-ld")

    resolved_shape = shape_path or _default_shape_path()
    shape_graph = Graph()
    shape_graph.parse(resolved_shape, format="turtle")

    conforms, results_graph, results_text = pyshacl.validate(
        data_graph,
        shacl_graph=shape_graph,
        inference="none",
        abort_on_first=False,
        allow_infos=False,
        allow_warnings=False,
    )

    if not conforms:
        failures = _parse_results(results_graph)
        raise TrustError(
            f"Offer failed SHACL validation: {results_text.strip()}",
            failures=failures,
        )


def _parse_results(results_graph: Graph) -> list[TrustFailure]:
    """Extract structured TrustFailures from a pyshacl results graph."""
    from rdflib import RDF, Namespace

    sh = Namespace(_SHACL_NS)

    def _str(graph: Graph, subject: Node, predicate: Node) -> str | None:
        value = graph.value(subject, predicate)
        if value is None:
            return None
        return _localname(str(value))

    failures: list[TrustFailure] = []
    for result in results_graph.subjects(RDF.type, sh.ValidationResult):
        failures.append(
            TrustFailure(
                message=_raw(results_graph.value(result, sh.resultMessage)),
                focus_node=_str(results_graph, result, sh.focusNode),
                result_path=_str(results_graph, result, sh.resultPath),
                value=_raw(results_graph.value(result, sh.value)),
                constraint=_str(results_graph, result, sh.sourceConstraintComponent),
                severity=_str(results_graph, result, sh.resultSeverity),
            )
        )
    return failures


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _raw(value: object) -> str | None:
    return None if value is None else str(value)


def _localname(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            return iri.rsplit(sep, 1)[-1]
    return iri
