# SYNTHETIC SAMPLE DATASET — "Acme Widgets Pty Ltd" (fictional)

This folder is a small, **entirely fictional** corpus written for development and
automated tests. It mirrors the *conventions* of the real (confidential) dataset —
folder layout, `- **Key:** value` metadata, shared identifiers, a superseded policy,
a stale procedure, a meeting decision, informal practice, dependent repetitions and a
CSV of records — without containing any of its content.

It is the default `DATA_DIR`, so the application runs out of the box. Point `DATA_DIR`
(or `HOST_DATA_DIR` for Docker) at the real dataset to use it instead.

`04_interviews/INT-21_robin_ash.md` deliberately contains a prompt-injection string to
test that retrieved text is treated as untrusted data.

This README is excluded from ingestion.
