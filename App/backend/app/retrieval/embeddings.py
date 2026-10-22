"""Optional sentence-transformer embeddings (lazy-loaded; absent -> semantic search disabled)."""

from __future__ import annotations

import logging
import threading
from functools import lru_cache

log = logging.getLogger(__name__)
_lock = threading.Lock()


class Embedder:
    def __init__(self, model_name: str, cache_dir: str | None = None):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self.error: str | None = None

    def _load(self):
        if self._model is None and self.error is None:
            with _lock:
                if self._model is None and self.error is None:
                    try:
                        from sentence_transformers import SentenceTransformer

                        self._model = SentenceTransformer(self.model_name, cache_folder=self.cache_dir, device="cpu")
                    except Exception as exc:  # noqa: BLE001 - model download/import failures disable the feature
                        self.error = f"{type(exc).__name__}: {exc}"
                        log.warning("Embedding model unavailable (%s); semantic search disabled", self.error)
        return self._model

    @property
    def available(self) -> bool:
        return self._load() is not None

    @property
    def dims(self) -> int | None:
        m = self._load()
        return int(m.get_sentence_embedding_dimension()) if m else None

    def encode(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        m = self._load()
        if m is None:
            raise RuntimeError(f"Embedding model unavailable: {self.error}")
        vecs = m.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


@lru_cache
def get_embedder() -> Embedder | None:
    from app.core.config import get_settings

    s = get_settings()
    if not s.embeddings_enabled:
        return None
    return Embedder(s.embedding_model, str(s.model_cache_dir) if s.model_cache_dir else None)
