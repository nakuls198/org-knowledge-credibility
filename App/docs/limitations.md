# Assumptions, limitations and model uncertainty

## What the outputs mean

* **The credibility score is a heuristic.** It summarises weighted evidence with configurable,
  documented assumptions (`config/credibility.yaml`). It is not a calibrated probability that a claim
  is true, and the UI says so wherever it is shown.
* **The interval is an evidence-weight interval** (Beta over support/contradiction mass). It reflects
  how much weighted evidence was found, not sampling uncertainty of a fitted model.
* **NLI and LLM confidences are signals, not probabilities.** They are recorded per passage for audit.

## Assumptions

* Authority depends on the kind of claim: approved policies/procedures are the best evidence of what
  a rule *is*; system records and first-hand accounts are the best evidence of what *happens*. The
  matrix values are judgement calls, tested only through the sensitivity analysis.
* A superseded document is weak evidence about the present but valid evidence about the past.
* A procedure last updated before the policy it implements took effect is *potentially* stale; the
  policy is treated as governing and the conflict is still flagged for review.
* A newer date never implies supersession on its own; only explicit metadata, explicit
  "superseded by" statements or documented rules create `supersedes` edges.
* Sources linked by follow-up / dependency / decision lineage, near-identical wording or the same
  speaker are not independent.
* Meeting discussion is not a decision; drafts and pilots are not implemented rules.

## Dataset constraints

* The corpus is small (~45 files, ~480 documents including CSV records) and synthetic-by-design for
  the capstone; some traps (e.g. the circulated meeting summary contradicting the minutes) are
  deliberate. Results may not transfer to real organisational archives.
* Only 4 claims carry client-supplied labels. The other 48 evaluation claims were drafted by the
  project author before the engine was run on them and **need independent review**; label bias in
  favour of the author's own model of the data is possible.
* There are no passage-level relevance or stance labels; retrieval and stance are evaluated at
  document level.
* Some structured "related document" links in the task CSV appear to be noise and are kept as
  low-weight `references` edges.

## Method limitations

* The rules classifier relies on lexical cues; it misses paraphrase, sarcasm and implicit negation.
  The NLI model compensates partly but can itself call off-topic text a contradiction (mitigated by a
  topical-relevance guard).
* Claims with mixed evidence frequently come out CONTESTED when the reference answer is SUPPORTED or
  CONTRADICTED.
* Actor recognition depends on roster tables and a small alias file; unknown roles are missed.
* Retrieval is limited to the indexed corpus; absent evidence is reported as insufficient evidence,
  never as a contradiction.
* The local LLM (`gemma4:e4b`) runs on CPU at roughly 40 s per passage, so it is offered as an opt-in
  deep-review mode and was not evaluated on the full claim set.
* Stage B (logistic regression) is trained on about 30 claims; it is a feasibility comparison, not a
  validated model. A Bayesian hierarchical model (PyMC) was deliberately not fitted because the data
  could not validate it.

## Operational limitations

* Single-user, no authentication; Elasticsearch security disabled for local use.
* Background jobs run in-process (no queue); a restart interrupts running jobs, which then stay in
  `running` state and must be re-run.
* Reproduction re-scores recorded evidence; re-running retrieval later can return different evidence
  if the corpus or configuration changed (changed sections are reported).
