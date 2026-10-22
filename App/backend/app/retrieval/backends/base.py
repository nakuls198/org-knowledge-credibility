"""Search backend interface. Elasticsearch in production; in-memory BM25 for tests/offline use."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.retrieval.schemas import SearchFilters


class RawHit(dict):
    """{'id', 'score', 'source': {...}, 'highlights': [...], 'matched': [...]}"""


class SearchBackend(ABC):
    name = "base"

    @abstractmethod
    def ensure_index(self, recreate: bool = False, vector_dims: int | None = None) -> None: ...

    @abstractmethod
    def index_exists(self) -> bool: ...

    @abstractmethod
    def upsert(self, docs: list[dict[str, Any]]) -> int: ...

    @abstractmethod
    def delete(self, ids: list[str]) -> int: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def lexical(self, query: str, filters: SearchFilters, size: int, offset: int = 0,
                highlight: bool = True, boost_identifiers: list[str] | None = None) -> tuple[int, list[RawHit]]: ...

    @abstractmethod
    def vector(self, vector: list[float], filters: SearchFilters, size: int) -> list[RawHit]: ...

    @abstractmethod
    def get(self, ids: list[str]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @property
    def supports_vectors(self) -> bool:
        return False
