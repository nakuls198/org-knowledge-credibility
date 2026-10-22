"""Pipeline: relationships, issues, incremental indexing, deletion, versioning."""

from sqlalchemy import select

from app.database.models import Document, IngestionIssue, Relationship, Section, SourceFile, SourceFileVersion
from app.ingestion.pipeline import new_job, run_job


def _rel(db, src, typ, tgt):
    return db.scalars(select(Relationship).where(Relationship.source_document_id == src, Relationship.rel_type == typ,
                                                 Relationship.target_document_id == tgt)).first()


def test_sample_corpus_ingests(ingested):
    db, backend, _, stats = ingested
    assert stats["files_new"] == 9 and stats["files_deleted"] == 0
    ids = {d.id for d in db.scalars(select(Document))}
    assert {"POL-10", "POL-09", "PROC-10", "MTG-10", "INT-20", "COMMS-20", "COMMS-21", "TASK-201"} <= ids
    assert "readme" not in ids  # README excluded by config
    assert backend.count() == stats["sections_indexed"] > 0


def test_explicit_and_inferred_relationships(ingested):
    db, *_ = ingested
    sup = _rel(db, "POL-10", "supersedes", "POL-09")
    assert sup is not None and sup.origin == "metadata" and not sup.is_inferred
    assert set(sup.attributes.get("origins", [])) == {"metadata"} and len(sup.attributes["declarations"]) == 2  # both sides
    assert _rel(db, "PROC-10", "implements", "POL-10") is not None
    assert _rel(db, "COMMS-20", "follows_up", "MTG-10") is not None
    assert _rel(db, "COMMS-21", "depends_on", "COMMS-20") is not None
    rd = _rel(db, "MTG-10", "records_decision", "POL-10")
    assert rd is not None and rd.is_inferred and rd.confidence < 1 and rd.evidence
    # "No one was assigned to update PROC-10" must NOT become a recorded decision
    assert _rel(db, "MTG-10", "records_decision", "PROC-10") is None
    # newer timestamp alone never implies supersession
    assert _rel(db, "POL-10", "supersedes", "PROC-10") is None


def test_quality_issues_reported(ingested):
    db, *_ = ingested
    issues = {(i.issue_type, i.document_id) for i in db.scalars(select(IngestionIssue))}
    assert ("broken_reference", "TASK-203") in issues  # references PROC-99
    assert ("stale_implementation", "PROC-10") in issues  # updated before POL-10 took effect
    proc = db.get(Document, "PROC-10")
    assert "potentially_stale" in proc.flags
    assert "superseded" in db.get(Document, "POL-09").flags


def test_incremental_is_idempotent(ingested, ingest_cfg):
    db, backend, corpus, _ = ingested
    job = run_job(db, new_job(db, corpus, "incremental"), ingest_cfg, backend)
    s = job.stats
    assert s["files_unchanged"] == 9 and s["documents_written"] == 0 and s["sections_indexed"] == 0


def test_modified_file_is_reindexed_and_versioned(ingested, ingest_cfg):
    db, backend, corpus, _ = ingested
    f = corpus / "03_process_docs" / "PROC-10_leave_request_process.md"
    f.write_text(f.read_text(encoding="utf-8").replace("longer than 5 days", "longer than 10 days"), encoding="utf-8")
    job = run_job(db, new_job(db, corpus, "incremental"), ingest_cfg, backend)
    assert job.stats["files_modified"] == 1 and job.stats["documents_written"] == 1
    sec = db.get(Section, "PROC-10:steps")
    assert "10 days" in sec.plain_text
    assert "10 days" in backend.get(["PROC-10:steps"])[0]["text"]
    sf = db.scalars(select(SourceFile).where(SourceFile.rel_path.like("%PROC-10%"))).one()
    versions = db.scalars(select(SourceFileVersion).where(SourceFileVersion.source_file_id == sf.id)).all()
    assert sf.version == 2 and [v.change_type for v in versions] == ["new", "modified"]


def test_deleted_file_soft_deletes_and_leaves_index(ingested, ingest_cfg):
    db, backend, corpus, _ = ingested
    (corpus / "04_interviews" / "INT-20_jamie_lee.md").unlink()
    job = run_job(db, new_job(db, corpus, "incremental"), ingest_cfg, backend)
    assert job.stats["files_deleted"] == 1
    doc = db.get(Document, "INT-20")
    assert doc.is_deleted and all(s.is_deleted for s in doc.sections)
    assert not backend.get([s.id for s in doc.sections])
    # restoring the file revives the same ids
    job = run_job(db, new_job(db, corpus, "incremental"), ingest_cfg, backend)
    assert job.stats["files_deleted"] == 0


def test_identity_collision_is_disambiguated(ingested, ingest_cfg):
    db, backend, corpus, _ = ingested
    dup = corpus / "02_policies" / "copy" / "POL-10_leave_policy_copy.md"
    dup.parent.mkdir()
    dup.write_text("# POL-10 — Leave Policy (copy)\n\n## Body\n\nDuplicate.\n", encoding="utf-8")
    run_job(db, new_job(db, corpus, "incremental"), ingest_cfg, backend)
    ids = [d.id for d in db.scalars(select(Document).where(Document.identifier == "POL-10"))]
    assert "POL-10" in ids and any(i.startswith("POL-10@") for i in ids)
    assert any(i.issue_type == "ambiguous_identity" for i in db.scalars(select(IngestionIssue)))


def test_circular_supersession_detected(db_session, corpus, ingest_cfg, memory_backend):
    (corpus / "02_policies" / "POL-30_a.md").write_text("# POL-30 — A\n\n- **Supersedes:** POL-31\n\n## Body\n\nA.\n", encoding="utf-8")
    (corpus / "02_policies" / "POL-31_b.md").write_text("# POL-31 — B\n\n- **Supersedes:** POL-30\n\n## Body\n\nB.\n", encoding="utf-8")
    job = run_job(db_session, new_job(db_session, corpus, "incremental"), ingest_cfg, memory_backend)
    cyc = [i for i in db_session.scalars(select(IngestionIssue).where(IngestionIssue.job_id == job.id))
           if i.issue_type == "circular_dependency"]
    assert cyc and set(cyc[0].details["documents"]) == {"POL-30", "POL-31"}


def test_parser_change_triggers_reparse(ingested, ingest_cfg):
    db, backend, corpus, _ = ingested
    cfg = {**ingest_cfg, "max_section_chars": 1700}  # config change -> new parser signature
    job = run_job(db, new_job(db, corpus, "incremental"), cfg, backend)
    assert job.stats["files_reparsed"] == 9
