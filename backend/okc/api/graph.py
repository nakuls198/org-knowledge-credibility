"""The evidence graph of an assessment: claim -> evidence -> document -> related documents."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from okc.api.converters import doc_summary, section_counts
from okc.api.schemas import GraphEdge, GraphNode, GraphOut
from okc.db.models import Assessment, Claim, Document, Relationship

_CLAIM_LABEL_CHARS, _EVIDENCE_LABEL_CHARS, _SPAN_CHARS = 120, 80, 400


def build_graph(db: Session, claim: Claim, assessment: Assessment | None, expand: list[str], rel_types: list[str],
                doc_types: list[str], labels: list[str], include_neutral: bool) -> GraphOut:
    nodes: dict[str, GraphNode] = {"claim": GraphNode(
        id="claim", type="claim", label=claim.text[:_CLAIM_LABEL_CHARS],
        data={"claim_id": claim.id, "verdict": claim.latest_verdict, "score": claim.latest_score,
              "needs_review": claim.needs_review, "text": claim.text})}
    if assessment is None:
        return GraphOut(claim_id=claim.id, assessment_id=None, nodes=list(nodes.values()), edges=[], filters={})

    edges: dict[str, GraphEdge] = {}
    evidence = [e for e in assessment.evidence if (include_neutral or e.label != "NEUTRAL") and (not labels or e.label in labels)]
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_({e.document_id for e in evidence})))}
    if doc_types:
        evidence = [e for e in evidence if docs.get(e.document_id) and docs[e.document_id].doc_type in doc_types]
    doc_ids = {e.document_id for e in evidence}

    for e in evidence:
        node_id = f"ev:{e.rank}"
        weight = (e.features or {}).get("weight")
        nodes[node_id] = GraphNode(id=node_id, type="evidence", label=f"E{e.rank}: {e.span_text[:_EVIDENCE_LABEL_CHARS]}",
                                   data={"rank": e.rank, "label": e.label, "scope": e.scope, "strength": e.strength,
                                         "weight": weight, "contribution": e.contribution, "cluster_id": e.cluster_id,
                                         "section_id": e.section_id, "document_id": e.document_id,
                                         "span": e.span_text[:_SPAN_CHARS], "rationale": e.rationale})
        edges[f"claim->{node_id}"] = GraphEdge(id=f"claim->{node_id}", source=node_id, target="claim", type=e.label.lower(),
                                               label=e.label.lower(), data={"weight": weight, "contribution": e.contribution})
        document_node = f"doc:{e.document_id}"
        edges[f"{node_id}->{document_node}"] = GraphEdge(id=f"{node_id}->{document_node}", source=node_id, target=document_node,
                                                         type="part_of", label="in", data={"section_id": e.section_id})

    counts = section_counts(db, list(docs))
    for doc_id in doc_ids:
        d = docs.get(doc_id)
        if d:
            nodes[f"doc:{doc_id}"] = GraphNode(id=f"doc:{doc_id}", type="document", label=f"{d.id}",
                                               data=doc_summary(d, counts.get(doc_id, 0)).model_dump(mode="json"))

    _add_relationships(db, nodes, edges, docs, counts, doc_ids | set(expand), set(expand), rel_types, doc_types)
    return GraphOut(claim_id=claim.id, assessment_id=assessment.id, nodes=list(nodes.values()), edges=list(edges.values()),
                    filters={"rel_types": sorted({e.type for e in edges.values()}),
                             "doc_types": sorted({n.data.get("doc_type") for n in nodes.values() if n.data.get("doc_type")})})


def _add_relationships(db: Session, nodes: dict[str, GraphNode], edges: dict[str, GraphEdge], docs: dict[str, Document],
                       counts: dict[str, int], frontier: set[str], expanded: set[str], rel_types: list[str],
                       doc_types: list[str]) -> None:
    """Relationships of the evidence documents (and any expanded documents) as document-to-document edges."""
    stmt = select(Relationship).where(
        Relationship.target_document_id.is_not(None),
        or_(Relationship.source_document_id.in_(frontier), Relationship.target_document_id.in_(frontier)),
        Relationship.rel_type != "external_reference")
    if rel_types:
        stmt = stmt.where(Relationship.rel_type.in_(rel_types))
    relationships = db.scalars(stmt).all()
    others = {r.source_document_id for r in relationships} | {r.target_document_id for r in relationships}
    other_docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(others - set(docs))))}
    other_counts = section_counts(db, list(other_docs))
    for r in relationships:
        for end in (r.source_document_id, r.target_document_id):
            if f"doc:{end}" in nodes:
                continue
            d = other_docs.get(end) or docs.get(end)
            if d is None or (doc_types and d.doc_type not in doc_types):
                continue
            nodes[f"doc:{end}"] = GraphNode(
                id=f"doc:{end}", type="related_document", label=end,
                data={**doc_summary(d, other_counts.get(end, counts.get(end, 0))).model_dump(mode="json"),
                      "expanded": end in expanded})
        source, target = f"doc:{r.source_document_id}", f"doc:{r.target_document_id}"
        if source in nodes and target in nodes:
            edges[r.id] = GraphEdge(id=r.id, source=source, target=target, type=r.rel_type, label=r.rel_type.replace("_", " "),
                                    data={"origin": r.origin, "inferred": r.is_inferred, "confidence": r.confidence,
                                          "evidence": r.evidence[:300], "attributes": r.attributes or {}})
