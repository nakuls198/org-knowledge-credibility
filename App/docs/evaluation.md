# Evaluation

All numbers here are produced by scripts in this repository; nothing is hand-entered.

* Main report: `python -m app.cli evaluate --split all --classifier rules --classifier rules+nli`
  → `evaluation/results/latest.json` (shown on the **Evaluation** page).
* Cross-version comparison: `evaluation/compare/` (see below) → `comparison.md`.

## Dataset

`evaluation/datasets/claims.csv` (built by `build_claims.py`), in the client's labelled-claims
format plus `claim_type`, `expected_verdict`, `split` and `label_source`:

| | Claims |
|---|---|
| Client-supplied labels (`labelled_claims_sample.csv`) | 4 |
| Author-drafted, **provisional** (written before running the engine) | 48 |
| Dev split (used for rule development) / test split (held out) | 29 / 23 (hash of claim id) |

Expected verdicts: supported, contradicted, contested, insufficient evidence. Expected supporting /
contradicting documents give document-level relevance and stance labels; there are no passage-level
labels.

**Discipline.** Rules and thresholds were changed only after inspecting dev-split claims
(`scripts/dev_eval.py` refuses to print test claims). The test split was first scored after
development stopped.

## Metrics

| Area | Metrics |
|---|---|
| Retrieval (BM25 / semantic / hybrid RRF) | document-level Recall@5/10/20, MRR, nDCG@10 against labelled sources |
| Evidence classification | document stance (S / C / mixed / none) vs labels: per-class precision/recall/F1, confusion matrix, evidence recall |
| Verdicts | accuracy (bootstrap 95% CI), macro-F1, confusion matrix, per split |
| Calibration | heuristic score vs `expected == SUPPORTED`: Brier (vs constant base rate), log loss, ECE, AUC, reliability bins — indicative only |
| Stage B | logistic regression on claim-level evidence features, trained on dev, scored on test; leave-one-out on all |
| Sensitivity | verdict flip rate and accuracy when authority, temporal, dependence, prior and threshold assumptions are perturbed |

## Development history (dev split, 29 claims)

| Step | Verdict accuracy |
|---|---|
| First end-to-end rules baseline | 28% |
| Fixed predicate negation, status parsing, claim typing, scope rules | 24–34% |
| Added NLI cross-encoder as a complementary signal | 55% |
| Whole-section entailment, topical guards, minimum-coverage rule | 62% (macro-F1 0.60) |

The largest single gain came from the NLI signal; the rules alone over-trust word overlap.

## Cross-version comparison (same 54 claims, same scoring)

`evaluation/compare/` runs each system in its own environment, read-only, on the same claims:
the 4 client claims, the 20 other held-out test claims (client claims that fall in the test split are counted under
"client") and the team's 30 gold claims from `credibility_engine_v2/data/gold`. Each system's labels
are mapped to one scheme (Supported/Likely → supported; Unsupported/Unlikely → contradicted;
Conflicted → contested; Not enough evidence → insufficient).

| System | Client (4) | Held-out test (20) | Team gold (30) | All (54) |
|---|---|---|---|---|
| `decidr_data_credibility` — offline keyword judge | 0/4 · binary 0% | 35% · binary 60% | 23% · binary 50% | 26% · binary 50% |
| `credibility_engine_v2` — NLI | 2/4 · binary 50% | 50% · binary 65% | 50% · binary 67% | 50% · binary 65% |
| **this version — rules + NLI** | **4/4 · binary 100%** | 50% · **binary 80%** | 50% · **binary 70%** | **54% · binary 76%** |

*4-class accuracy · binary = credible (supported) vs not credible.*

**Reading this fairly**

* The improvement is real but modest: equal 4-class accuracy on the held-out and team sets, better
  on the client claims and on the credible / not-credible decision.
* The **team gold claims are the fairest benchmark** — written by the team, unseen by this version.
  `credibility_engine_v2`'s rulebook was written with knowledge of those claims, which favours it there.
* The **held-out test labels were written by this version's author**, which may favour this version.
* `decidr_data_credibility` was run with its offline keyword judge only. Its intended LLM judge sends
  passages to Gemini/Groq; it was not run because the dataset is to stay local. Its real performance
  is therefore likely higher than shown.
* With 4–30 claims per column, one claim moves accuracy by 3–25 points; differences of a few points
  are not significant.

## Main report results

Filled from `evaluation/results/latest.json` (see the Evaluation page for every table).

Run of 2026-10-14 (`evaluation/results/eval_20261014T061603Z.json`), 52 claims, SQLite + in-memory
index with MiniLM embeddings (same retrieval logic as Elasticsearch).

### Retrieval (document level, claims with labelled sources)

| Mode | Recall@5 | Recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|
| BM25 | 0.59 | 0.69 | 0.76 | 0.63 |
| Semantic (MiniLM) | **0.73** | **0.82** | 0.86 | **0.75** |
| Hybrid (RRF) | 0.70 | 0.78 | **0.88** | 0.73 |

Semantic and hybrid retrieval beat BM25 on this corpus, so the evidence supports enabling them; the
claim investigation still uses BM25 plus counter-evidence and relationship expansion by default
(evidence recall 77%), which is configurable in `config/retrieval.yaml`.

### Verdicts and evidence stance

| Classifier | Verdict accuracy dev | **test (held out)** | macro-F1 test | Stance accuracy test | Evidence recall |
|---|---|---|---|---|---|
| Rules only | 38% | 52% (95% CI 35–74%) | 0.36 | 79% | 73% |
| **Rules + NLI** (default) | 62% | **57% (95% CI 39–74%)** | 0.45 | 89% | 73% |

### Calibration of the heuristic score (target: expected verdict = supported) — indicative

| Classifier | Brier test | constant base-rate Brier | ECE | AUC |
|---|---|---|---|---|
| Rules only | 0.214 | 0.250 | 0.20 | 0.81 |
| Rules + NLI | **0.145** | 0.250 | 0.16 | **0.86** |

### Stage B: logistic regression (trained on dev, scored on test)

| Model | Test Brier | Test AUC | Leave-one-out Brier (all) |
|---|---|---|---|
| Baseline heuristic score (rules + NLI) | **0.145** | **0.86** | **0.163** |
| Logistic regression on evidence features | 0.199 | 0.80 | 0.205 |

With ~30 training claims the supervised model does not beat the transparent baseline, so the
baseline remains the reported method. A Bayesian model was not fitted for the same reason.

### Sensitivity (rules + NLI, share of the 52 verdicts that change)

| Perturbation | Flip rate |
|---|---|
| Formal / practice authority ×0.75 or ×1.25 | 0–6% |
| Testimony / belief authority ±25% | 0–2% |
| Equal authority for every evidence scope | 17% |
| No temporal discounting | 2% |
| Ignore dependent sources / no independence model | 4% / 4% |
| Verdict thresholds ±0.05 | 10% |
| Prior pseudo-counts 0.25 → 1.0 | 44% |

Verdicts are robust to moderate changes in individual weights, but sensitive to the prior strength and
to removing the claim-type authority model entirely. The prior was set on the dev split; this
sensitivity is the main assumption to review.
