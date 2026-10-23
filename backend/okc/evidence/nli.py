"""Optional NLI cross-encoder signal (premise = evidence unit, hypothesis = claim).

The scores are softmax outputs of a model trained on generic NLI data. They are a complementary
signal, NOT calibrated probabilities that the claim is true.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache

from okc.settings import get_settings

log = logging.getLogger(__name__)
_lock = threading.Lock()


class NliScorer:
    def __init__(self, model_name: str, cache_dir: str | None = None):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._labels: list[str] = []
        self.error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self._model is None and self.error is None:
            with _lock:
                if self._model is None and self.error is None:
                    try:
                        from sentence_transformers import CrossEncoder

                        self._model = CrossEncoder(self.model_name, device="cpu", cache_dir=self.cache_dir)
                        id2label = self._model.model.config.id2label
                        self._labels = [id2label[i].lower() for i in range(len(id2label))]
                    except Exception as exc:  # noqa: BLE001 - a missing package or model disables the feature
                        self.error = f"{type(exc).__name__}: {exc}"
                        log.warning("NLI model unavailable (%s)", self.error)
        return self._model

    @property
    def available(self) -> bool:
        return self._load() is not None

    def score(self, pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        model = self._load()
        if model is None or not pairs:
            return [{} for _ in pairs]
        import numpy as np

        logits = np.atleast_2d(model.predict(pairs, apply_softmax=False, show_progress_bar=False))
        exp = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = exp / exp.sum(axis=1, keepdims=True)
        out = []
        for row in probs:
            d = {label: float(p) for label, p in zip(self._labels, row)}
            out.append({"entailment": d.get("entailment", 0.0), "contradiction": d.get("contradiction", 0.0),
                        "neutral": d.get("neutral", 0.0)})
        return out


@lru_cache
def get_nli() -> NliScorer | None:
    s = get_settings()
    if not s.nli_enabled:
        return None
    return NliScorer(s.nli_model, str(s.model_cache_dir) if s.model_cache_dir else None)
