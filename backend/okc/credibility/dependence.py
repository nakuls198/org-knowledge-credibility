"""Source independence: which evidence items are not independent confirmations of each other.

Items in the same dependence cluster are not independent: the same document, documents linked by a
follow-up / dependency / decision lineage, near-duplicate wording or meaning, or repeated testimony of
one person. A cluster counts its strongest item fully and the rest at `dependent_discount`.
"""

from __future__ import annotations

import logging
from typing import Any

from okc.common.text import jaccard, shingles
from okc.credibility.models import EvidenceItem, RelationEdge

log = logging.getLogger(__name__)


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.reasons: dict[int, list[str]] = {}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int, why: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
            self.reasons.setdefault(ra, []).extend(self.reasons.pop(rb, []))
        self.reasons.setdefault(ra, []).append(why)


def _is_record(item: EvidenceItem) -> bool:
    return item.ctx.doc_kind == "record"


def cluster_items(cfg: dict[str, Any], items: list[EvidenceItem], edges: list[RelationEdge],
                  embedder=None) -> tuple[UnionFind, list[str]]:
    """(clusters, notes explaining why items were merged)."""
    dep = cfg["dependence"]
    uf = UnionFind(len(items))
    notes: list[str] = []

    by_doc: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        by_doc.setdefault(it.ctx.document_id, []).append(i)
    for doc_id, idx in by_doc.items():
        for k in idx[1:]:
            uf.union(idx[0], k, f"same document {doc_id}")

    link_types = set(dep.get("link_types", []))
    for e in edges:
        if e.rel_type in link_types and e.source in by_doc and e.target in by_doc:
            uf.union(by_doc[e.source][0], by_doc[e.target][0], f"{e.source} {e.rel_type} {e.target} ({e.origin})")
            notes.append(f"{e.source} and {e.target} are linked ({e.rel_type}, {e.origin}); counted as one source lineage")

    _merge_near_duplicates(uf, notes, items, float(dep.get("near_duplicate_jaccard", 0.5)))
    if embedder is not None and len(items) > 1:
        _merge_semantic_duplicates(uf, notes, items, embedder, float(dep.get("semantic_duplicate_cosine", 0.82)))
    _merge_repeated_speakers(uf, items, set(dep.get("same_person_scopes", [])))
    return uf, sorted(set(notes))


def _merge_near_duplicates(uf: UnionFind, notes: list[str], items: list[EvidenceItem], threshold: float) -> None:
    sh = [shingles(it.ctx.span) for it in items]
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if items[a].ctx.document_id == items[b].ctx.document_id:
                continue
            if _is_record(items[a]) or _is_record(items[b]):
                continue  # templated system records look alike but record independent events
            similarity = jaccard(sh[a], sh[b])
            if similarity >= threshold:
                uf.union(a, b, f"near-duplicate wording (Jaccard {similarity:.2f})")
                notes.append(f"{items[a].ctx.document_id} and {items[b].ctx.document_id} repeat near-identical wording")


def _merge_semantic_duplicates(uf: UnionFind, notes: list[str], items: list[EvidenceItem], embedder, threshold: float) -> None:
    try:
        import numpy as np

        vectors = np.asarray(embedder.encode([it.ctx.span[:500] for it in items]))
        similarity = vectors @ vectors.T
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                if _is_record(items[a]) or _is_record(items[b]):
                    continue
                if (items[a].ctx.document_id != items[b].ctx.document_id and similarity[a, b] >= threshold
                        and items[a].judgement.label == items[b].judgement.label):
                    uf.union(a, b, f"semantically near-identical (cosine {similarity[a, b]:.2f})")
                    notes.append(f"{items[a].ctx.document_id} and {items[b].ctx.document_id} make the same "
                                 f"statement in near-identical terms (cosine {similarity[a, b]:.2f})")
    except Exception as exc:  # noqa: BLE001 - dependence detection degrades gracefully
        log.info("Semantic duplicate detection skipped: %s", type(exc).__name__)


def _merge_repeated_speakers(uf: UnionFind, items: list[EvidenceItem], scopes: set[str]) -> None:
    """Several statements by one interviewee are one source."""
    first_by_person: dict[str, int] = {}
    for i, it in enumerate(items):
        if it.judgement.scope in scopes and it.ctx.doc_people and it.ctx.doc_type == "interview":
            person = it.ctx.doc_people[0]
            if person in first_by_person:
                uf.union(first_by_person[person], i, f"repeated statements by {person}")
            else:
                first_by_person[person] = i
