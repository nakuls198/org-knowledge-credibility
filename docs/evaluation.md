# Evaluation

All numbers come from scripts in this repository; nothing is entered by hand.

```bash
python -m okc.cli evaluate --split all --classifier rules --classifier rules+nli
```

writes `evaluation/results/latest.json` (and a timestamped copy). Earlier saved runs are in `evaluation/results/`.

## Dataset

`evaluation/datasets/claims.csv` (built by `build_claims.py`) uses the client's labelled-claims format plus
`claim_type`, `expected_verdict`, `split` and `label_source`:

| | Claims |
|---|---|
| Client-supplied labels | 4 |
| Author-drafted, provisional (written before running the engine) | 48 |
| Dev split (used for rule development) / test split (held out) | 29 / 23, by hash of the claim id |

Expected verdicts: supported, contradicted, contested, insufficient evidence. Expected supporting and
contradicting documents give document-level relevance and stance labels; there are no passage-level labels.

**Discipline.** Rules and thresholds were changed only after inspecting dev-split claims. The test split was first scored after development stopped.

## Metrics

| Area | Metrics |
|---|---|
| Retrieval (BM25 / semantic / hybrid) | document-level Recall@5/10/20, MRR, nDCG@10 |
| Evidence classification | document stance vs labels: per-class precision/recall/F1, confusion matrix, evidence recall |
| Verdicts | accuracy (bootstrap 95% CI), macro-F1, confusion matrix, per split |
| Calibration | heuristic score vs `expected == SUPPORTED`: Brier (vs constant base rate), log loss, ECE, AUC, reliability bins; indicative only |
| Stage B | logistic regression on claim-level evidence features, trained on dev, scored on test |
| Sensitivity | verdict flip rate when authority, temporal, dependence, prior and threshold assumptions are perturbed |

## Reading the results

* With 52 claims, one claim moves accuracy by about 2 points; the test split has 23 claims, so differences of a few points are not significant.
* The NLI signal gave the largest single gain over the rules alone, which over-trust word overlap.
* The supervised Stage B model did not beat the transparent baseline with about 30 training claims, so the baseline remains the reported method.
* Verdicts are robust to moderate changes in individual weights but sensitive to the prior strength; that is the main assumption to review.
