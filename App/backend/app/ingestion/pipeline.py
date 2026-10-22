"""Ingestion pipeline: discover -> diff -> parse -> persist -> resolve -> index.

Idempotent and incremental:
* files are compared by SHA-256; unchanged files are not rewritten or re-indexed
* deleted files soft-delete their documents/sections and are removed from the index
* every distinct file version is kept in `source_file_versions`
* relationships and corpus-level issues are recomputed over the whole corpus each run
  (cheap, and keeps cross-file references consistent when a target appears/disappears)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.text import sha256, short_hash
from app.database.models import (
    Document,
    IngestionIssue,
    IngestionJob,
    Relationship,
    Section,
    SourceFile,
    SourceFileVersion,
)
from app.ingestion.csv_parser import CsvParser
from app.ingestion.markdown_parser import MarkdownParser
from app.ingestion.metadata import path_matches
from app.ingestion.parsed import ParsedDocument, ParsedFile, ParseIssue
from app.relationships.resolver import RelationshipResolver
from app.retrieval.backends.base import SearchBackend
from app.retrieval.embeddings import Embedder

log = logging.getLogger(__name__)

# Bump when parsing logic changes; together with the ingestion config it forms the parser
# signature. A file whose stored signature differs is re-parsed even if its bytes are unchanged.
PARSER_VERSION = "parser-1.2"


def parser_signature(cfg: dict[str, Any]) -> str:
    import json

    return short_hash(PARSER_VERSION + json.dumps(cfg, sort_keys=True, default=str), 24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DiscoveredFile:
    rel_path: str
    abs_path: Path
    content: str
    content_hash: str
    encoding: str
    size: int


@dataclass
class IngestionStats:
    files_new: int = 0
    files_modified: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    files_reparsed: int = 0
    documents_active: int = 0
    sections_active: int = 0
    documents_written: int = 0
    sections_written: int = 0
    sections_indexed: int = 0
    sections_removed_from_index: int = 0
    relationships: int = 0
    issues: int = 0
    index_rebuilt: bool = False
    embeddings: bool = False
    reference_date: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def discover(data_dir: Path, cfg: dict[str, Any]) -> tuple[list[DiscoveredFile], list[ParseIssue]]:
    issues: list[ParseIssue] = []
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")
    out: list[DiscoveredFile] = []
    for p in sorted(data_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(data_dir).as_posix()
        if not any(path_matches(rel, g) for g in cfg.get("include_globs", ["**/*.md"])):
            continue
        if any(path_matches(rel, g) for g in cfg.get("exclude_globs", [])):
            continue
        raw = p.read_bytes()
        enc = "utf-8"
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text, enc = raw.decode("cp1252", errors="replace"), "cp1252"
            issues.append(ParseIssue("warning", "non_utf8_encoding", "File is not valid UTF-8; decoded as cp1252",
                                     rel_path=rel))
        out.append(DiscoveredFile(rel, p, text, sha256(raw), enc, len(raw)))
    return out, issues


def section_search_doc(doc: Document, sec: Section, embedding: list[float] | None = None) -> dict[str, Any]:
    d = {
        "section_id": sec.id, "document_id": doc.id, "parent_section_id": sec.parent_section_id,
        "doc_title": doc.title, "doc_type": doc.doc_type, "doc_kind": doc.kind, "status": doc.status,
        "flags": list(doc.flags or []), "owner": doc.owner, "people": list(doc.people or []),
        "doc_date": doc.doc_date.isoformat() if doc.doc_date else None,
        "identifiers": sorted(set(([doc.identifier] if doc.identifier else []) + list(sec.identifiers or []))),
        "heading": sec.heading, "heading_path": list(sec.heading_path or []), "section_role": sec.section_role,
        "text": sec.plain_text, "source_path": doc.rel_path, "start_line": sec.start_line, "end_line": sec.end_line,
        "content_hash": sec.content_hash,
    }
    if embedding is not None:
        d["embedding"] = embedding
    return d


def indexable_sections(doc: Document) -> list[Section]:
    """Leaf sections only: a chunked parent is represented by its chunks."""
    secs = [s for s in doc.sections if not s.is_deleted]
    parents = {s.parent_section_id for s in secs if s.parent_section_id}
    return [s for s in secs if s.id not in parents]


class IngestionPipeline:
    def __init__(self, db: Session, cfg: dict[str, Any], backend: SearchBackend | None, embedder: Embedder | None = None):
        self.db = db
        self.cfg = cfg
        self.backend = backend
        self.embedder = embedder if (embedder is not None and embedder.available) else None
        self.md = MarkdownParser(cfg)
        self.csv = CsvParser(cfg)
        self.signature = parser_signature(cfg)

    # ------------------------------------------------------------------ entry
    def run(self, job: IngestionJob, data_dir: Path, mode: str = "incremental") -> IngestionStats:
        stats = IngestionStats(embeddings=self.embedder is not None)
        files, issues = discover(data_dir, self.cfg)
        existing = {sf.rel_path: sf for sf in self.db.scalars(select(SourceFile))}
        seen: set[str] = set()
        changed_doc_ids: set[str] = set()
        removed_section_ids: set[str] = set()

        parsed: dict[str, ParsedFile] = {}
        for f in files:
            pf = self._parse(f)
            parsed[f.rel_path] = pf
            issues.extend(pf.issues)

        for f in files:
            seen.add(f.rel_path)
            sf = existing.get(f.rel_path)
            if sf is None or sf.is_deleted:
                change = "new"
                stats.files_new += 1
            elif sf.content_hash != f.content_hash:
                change = "modified"
                stats.files_modified += 1
            elif sf.parser_signature != self.signature:
                change = "reparsed"  # same bytes, new parser / config
                stats.files_reparsed += 1
            else:
                change = "unchanged"
                stats.files_unchanged += 1
            if change == "unchanged" and mode != "full":
                continue
            sf = self._upsert_source_file(sf, f, change)
            w_docs, w_secs, removed = self._persist_documents(sf, parsed[f.rel_path])
            changed_doc_ids |= w_docs
            removed_section_ids |= removed
            stats.documents_written += len(w_docs)
            stats.sections_written += w_secs

        for rel, sf in existing.items():
            if rel not in seen and not sf.is_deleted:
                stats.files_deleted += 1
                removed_section_ids |= self._delete_source_file(sf)

        self.db.flush()
        # ---- corpus-wide resolution
        docs = list(self.db.scalars(select(Document).where(Document.is_deleted.is_(False))
                                    .options(selectinload(Document.sections))))
        result = RelationshipResolver(self.cfg).resolve(docs)
        stats.reference_date = result.reference_date.isoformat() if result.reference_date else None
        flag_changed = self._apply_flags(docs, result.doc_flags)
        changed_doc_ids |= flag_changed
        self.db.execute(delete(Relationship))
        for e in result.edges:
            self.db.add(Relationship(
                id=e.id, source_document_id=e.source, target_document_id=e.target, target_ref=e.target_ref,
                rel_type=e.rel_type, origin=e.origin, is_inferred=e.origin == "inferred", confidence=e.confidence,
                evidence=e.evidence, source_section_id=e.section_id, resolved=e.target is not None,
                attributes=e.attributes))
        issues.extend(result.issues)
        for i in issues:
            self.db.add(IngestionIssue(job_id=job.id, severity=i.severity, issue_type=i.issue_type,
                                       document_id=i.document_id, section_id=i.section_id, rel_path=i.rel_path,
                                       message=i.message, details=i.details))
        stats.relationships = len(result.edges)
        stats.issues = len(issues)
        stats.documents_active = len(docs)
        stats.sections_active = sum(len([s for s in d.sections if not s.is_deleted]) for d in docs)
        self.db.flush()

        # ---- search index (derived; rebuildable)
        if self.backend is not None:
            self._index(docs, changed_doc_ids, removed_section_ids, mode, stats)
        return stats

    # ---------------------------------------------------------------- parsing
    def _parse(self, f: DiscoveredFile) -> ParsedFile:
        if f.rel_path.lower().endswith(".csv"):
            return self.csv.parse(f.rel_path, f.content, f.content_hash, f.encoding)
        return self.md.parse(f.rel_path, f.content, f.content_hash, f.encoding)

    # ------------------------------------------------------------ persistence
    def _upsert_source_file(self, sf: SourceFile | None, f: DiscoveredFile, change: str) -> SourceFile:
        now = _now()
        if sf is None:
            sf = SourceFile(id="sf_" + short_hash(f.rel_path), rel_path=f.rel_path, parser_signature=self.signature,
                            file_kind="csv" if f.rel_path.lower().endswith(".csv") else "markdown",
                            content_hash=f.content_hash, size_bytes=f.size, encoding=f.encoding,
                            raw_content=f.content, version=1, first_seen_at=now, last_seen_at=now, last_changed_at=now)
            self.db.add(sf)
        else:
            if change in ("new", "modified"):
                sf.version = (sf.version or 0) + 1
                sf.last_changed_at = now
            sf.content_hash, sf.size_bytes, sf.encoding, sf.raw_content = f.content_hash, f.size, f.encoding, f.content
            sf.is_deleted = False
            sf.last_seen_at = now
            sf.parser_signature = self.signature
        if change in ("new", "modified"):
            self.db.add(SourceFileVersion(source_file_id=sf.id, version=sf.version, content_hash=f.content_hash,
                                          raw_content=f.content, change_type=change, seen_at=now))
        self.db.flush()
        return sf

    def _persist_documents(self, sf: SourceFile, pf: ParsedFile) -> tuple[set[str], int, set[str]]:
        written_docs: set[str] = set()
        removed_sections: set[str] = set()
        n_secs = 0
        old_docs = {d.id: d for d in self.db.scalars(select(Document).where(Document.source_file_id == sf.id)
                                                     .options(selectinload(Document.sections)))}
        new_ids: set[str] = set()
        for pd in pf.documents:
            pd = self._resolve_identity_collision(pd, sf)
            new_ids.add(pd.id)
            doc = old_docs.get(pd.id) or self.db.get(Document, pd.id)
            if doc is None:
                doc = Document(id=pd.id)
                self.db.add(doc)
            elif (doc.content_hash == pd.content_hash and not doc.is_deleted and doc.source_file_id == sf.id
                  and (doc.meta or {}).get("_parser_signature") == self.signature):
                continue  # document unchanged inside a modified file
            self._copy_doc(doc, pd, sf, self.signature)
            written_docs.add(doc.id)
            old_secs = {s.id: s for s in doc.sections}
            keep: set[str] = set()
            for ps in pd.sections:
                keep.add(ps.id)
                s = old_secs.get(ps.id)
                if s is None:
                    s = Section(id=ps.id)
                    doc.sections.append(s)
                for attr in ("parent_section_id", "ordinal", "heading", "heading_path", "level", "section_role", "text",
                             "plain_text", "start_line", "end_line", "start_char", "end_char", "content_hash",
                             "identifiers"):
                    setattr(s, attr, getattr(ps, attr))
                s.is_deleted = False
                n_secs += 1
            for sid, s in old_secs.items():
                if sid not in keep and not s.is_deleted:
                    s.is_deleted = True
                    removed_sections.add(sid)
        for did, d in old_docs.items():
            if did not in new_ids and not d.is_deleted:
                d.is_deleted = True
                d.updated_at = _now()
                for s in d.sections:
                    if not s.is_deleted:
                        s.is_deleted = True
                        removed_sections.add(s.id)
        return written_docs, n_secs, removed_sections

    def _resolve_identity_collision(self, pd: ParsedDocument, sf: SourceFile) -> ParsedDocument:
        other = self.db.get(Document, pd.id)
        if other is not None and other.source_file_id != sf.id and not other.is_deleted:
            other_sf = self.db.get(SourceFile, other.source_file_id)
            if other_sf is not None and not other_sf.is_deleted:
                from app.core.text import slugify

                new_id = f"{pd.id}@{slugify(pd.rel_path.rsplit('.', 1)[0], 40)}"
                for s in pd.sections:
                    s.id = new_id + s.id[len(pd.id):]
                    if s.parent_section_id:
                        s.parent_section_id = new_id + s.parent_section_id[len(pd.id):]
                pd.id = new_id
        return pd

    @staticmethod
    def _copy_doc(doc: Document, pd: ParsedDocument, sf: SourceFile, signature: str) -> None:
        doc.source_file_id = sf.id
        for attr in ("rel_path", "kind", "identifier", "title", "doc_type", "doc_type_source", "status", "status_source",
                     "owner", "people", "version_label", "doc_date", "date_source", "effective_date", "review_date",
                     "superseded_date", "start_line", "end_line", "content_hash"):
            setattr(doc, attr, getattr(pd, attr))
        doc.meta = {**pd.meta, "_parse_flags": list(pd.flags), "_parser_signature": signature}
        doc.flags = list(pd.flags)
        doc.is_deleted = False
        doc.updated_at = _now()

    def _delete_source_file(self, sf: SourceFile) -> set[str]:
        removed: set[str] = set()
        sf.is_deleted = True
        sf.last_changed_at = _now()
        self.db.add(SourceFileVersion(source_file_id=sf.id, version=sf.version + 1, content_hash=sf.content_hash,
                                      raw_content="", change_type="deleted", seen_at=_now()))
        sf.version += 1
        for d in self.db.scalars(select(Document).where(Document.source_file_id == sf.id)):
            d.is_deleted = True
            for s in d.sections:
                if not s.is_deleted:
                    s.is_deleted = True
                    removed.add(s.id)
        return removed

    @staticmethod
    def _apply_flags(docs: list[Document], resolved: dict[str, list[str]]) -> set[str]:
        changed: set[str] = set()
        for d in docs:
            new = sorted(set((d.meta or {}).get("_parse_flags", [])) | set(resolved.get(d.id, [])))
            if sorted(d.flags or []) != new:
                d.flags = new
                changed.add(d.id)
        return changed

    # --------------------------------------------------------------- indexing
    def _index(self, docs: list[Document], changed: set[str], removed: set[str], mode: str, stats: IngestionStats):
        dims = self.embedder.dims if self.embedder else None
        rebuild = mode == "full" or not self.backend.index_exists()
        if not rebuild and self.embedder and not self.backend.supports_vectors:
            rebuild = True  # embeddings newly enabled -> mapping needs the vector field
            stats.warnings.append("Index rebuilt to add the embedding field")
        if rebuild:
            self.backend.ensure_index(recreate=True, vector_dims=dims)
            stats.index_rebuilt = True
            targets = docs
        else:
            targets = [d for d in docs if d.id in changed]
        payload: list[tuple[Document, Section]] = [(d, s) for d in targets for s in indexable_sections(d)]
        if not rebuild:
            # a chunked parent must not linger in the index
            for d in targets:
                parents = {s.parent_section_id for s in d.sections if s.parent_section_id}
                removed |= parents
        vectors: list[list[float] | None] = [None] * len(payload)
        if self.embedder and payload:
            texts = [f"{d.title}. {s.heading}. {s.plain_text}"[:2000] for d, s in payload]
            vectors = self.embedder.encode(texts)
        batch = [section_search_doc(d, s, v) for (d, s), v in zip(payload, vectors)]
        for i in range(0, len(batch), 500):
            stats.sections_indexed += self.backend.upsert(batch[i:i + 500])
        if not rebuild and removed:
            stats.sections_removed_from_index = self.backend.delete(sorted(removed))


def new_job(db: Session, data_dir: Path, mode: str) -> IngestionJob:
    job = IngestionJob(id=str(uuid.uuid4()), status="queued", mode=mode, data_dir=str(data_dir))
    db.add(job)
    db.flush()
    return job


def run_job(db: Session, job: IngestionJob, cfg: dict[str, Any], backend: SearchBackend | None,
            embedder: Embedder | None = None) -> IngestionJob:
    job.status, job.started_at = "running", _now()
    db.commit()
    try:
        stats = IngestionPipeline(db, cfg, backend, embedder).run(job, Path(job.data_dir), job.mode)
        job.stats = stats.as_dict()
        job.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 - recorded on the job for the UI
        db.rollback()
        log.exception("Ingestion job %s failed", job.id)
        job = db.get(IngestionJob, job.id) or job
        job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
    job.finished_at = _now()
    db.commit()
    return job
