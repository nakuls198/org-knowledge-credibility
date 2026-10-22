"""Metric implementations (small, dependency-light, unit-tested)."""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Callable, Sequence


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return float("nan")
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    for i, d in enumerate(ranked, start=1):
        if d in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: Sequence[str], gains: dict[str, float], k: int) -> float:
    if not gains:
        return float("nan")
    dcg = sum(gains.get(d, 0.0) / math.log2(i + 1) for i, d in enumerate(ranked[:k], start=1))
    ideal = sorted(gains.values(), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def nanmean(xs: Sequence[float]) -> float:
    v = [x for x in xs if not (isinstance(x, float) and math.isnan(x))]
    return sum(v) / len(v) if v else float("nan")


def confusion(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]) -> list[list[int]]:
    idx = {l: i for i, l in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for t, p in zip(y_true, y_pred):
        if t in idx and p in idx:
            m[idx[t]][idx[p]] += 1
    return m


def per_class(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]) -> dict[str, dict[str, float]]:
    out = {}
    for l in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == l and p == l)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != l and p == l)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == l and p != l)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[l] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "support": tp + fn}
    return out


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]) -> float:
    pc = per_class(y_true, y_pred, labels)
    present = [l for l in labels if pc[l]["support"] > 0]
    return sum(pc[l]["f1"] for l in present) / len(present) if present else 0.0


def accuracy(y_true: Sequence, y_pred: Sequence) -> float:
    return sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true) if y_true else float("nan")


def brier(y: Sequence[int], p: Sequence[float]) -> float:
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / len(y)


def log_loss(y: Sequence[int], p: Sequence[float], eps: float = 1e-6) -> float:
    return -sum(yi * math.log(max(eps, pi)) + (1 - yi) * math.log(max(eps, 1 - pi)) for yi, pi in zip(y, p)) / len(y)


def reliability(y: Sequence[int], p: Sequence[float], bins: int = 5) -> tuple[list[dict], float]:
    """Equal-width reliability bins and expected calibration error (ECE)."""
    rows, ece, n = [], 0.0, len(y)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(yi, pi) for yi, pi in zip(y, p) if (lo <= pi < hi) or (b == bins - 1 and pi == 1.0)]
        if not sel:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": 0, "mean_score": None, "observed_rate": None})
            continue
        ms = sum(pi for _, pi in sel) / len(sel)
        obs = sum(yi for yi, _ in sel) / len(sel)
        ece += len(sel) / n * abs(ms - obs)
        rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "mean_score": round(ms, 3), "observed_rate": round(obs, 3)})
    return rows, ece


def auc(y: Sequence[int], p: Sequence[float]) -> float:
    pos = [pi for yi, pi in zip(y, p) if yi == 1]
    neg = [pi for yi, pi in zip(y, p) if yi == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def bootstrap_ci(values_fn: Callable[[list[int]], float], n: int, reps: int = 1000, seed: int = 13,
                 alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap CI over item indexes."""
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        try:
            v = values_fn(idx)
        except ZeroDivisionError:
            continue
        if not (isinstance(v, float) and math.isnan(v)):
            stats.append(v)
    if not stats:
        return float("nan"), float("nan")
    stats.sort()
    return stats[int(alpha / 2 * len(stats))], stats[min(len(stats) - 1, int((1 - alpha / 2) * len(stats)))]


def label_counts(xs: Sequence[str]) -> dict[str, int]:
    return dict(Counter(xs))
