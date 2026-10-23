# Assumptions, limitations and model uncertainty

## What the outputs mean

* **The credibility score is a heuristic.** It summarises weighted evidence with configurable, documented assumptions (`config/credibility.yaml`). It is not a calibrated probability that a claim is true.
* **The interval is an evidence-weight interval** (Beta over support/contradiction mass). It reflects how much weighted evidence was found, not the sampling uncertainty of a fitted model.
* **NLI and LLM confidences are signals, not probabilities.** They are recorded per passage for audit.

## Assumptions

* Authority depends on the kind of claim: approved policies and procedures are the best evidence of what a rule *is*; system records and first-hand accounts are the best evidence of what *happens*. The matrix values are judgement calls, tested only through the sensitivity analysis.
* A superseded document is weak evidence about the present but valid evidence about the past.
* A procedure last updated before the policy it implements took effect is *potentially* stale; the policy governs and the conflict is still flagged for review.
* A newer date never implies supersession on its own; only explicit metadata, explicit "superseded by" statements or documented rules create `supersedes` edges.
* Sources linked by follow-up, dependency or decision lineage, near-identical wording or the same speaker are not independent.
* Meeting discussion is not a decision; drafts and pilots are not implemented rules.

## Dataset constraints

* The corpus is small (about 45 files, about 480 documents including CSV records) and synthetic by design; some traps (such as a circulated meeting summary contradicting the minutes) are deliberate. Results may not transfer to real organisational archives.
* Only 4 claims carry client-supplied labels. The other 48 were drafted by the project author before the engine was run on them and need independent review.
* There are no passage-level relevance or stance labels.
* Some structured "related document" links in the task CSV appear to be noise and are kept as low-weight `references` edges.

## Method limitations

* The rules classifier relies on lexical cues; it misses paraphrase, sarcasm and implicit negation. The NLI model compensates partly but can call off-topic text a contradiction (mitigated by a topical-relevance guard).
* Claims with mixed evidence often come out CONTESTED when the reference answer is SUPPORTED or CONTRADICTED.
* Actor recognition depends on roster tables and a small alias file; unknown roles are missed.
* Retrieval is limited to the indexed corpus; absent evidence is reported as insufficient evidence, never as a contradiction.
* The local LLM runs on CPU at roughly 40 s per passage, so it is opt-in and was not evaluated on the full claim set.
* The NLI and embedding paths are covered by unit tests with fake models; only the rules path has been compared line for line against the previous build.

## Operational limitations

* Single user, no authentication; Elasticsearch security is disabled for local use.
* Background jobs run in-process; a restart interrupts running jobs, which then stay in `running` and must be re-run.
* Reproduction re-scores recorded evidence; re-running retrieval later can return different evidence if the corpus or configuration changed (changed sections are reported).
