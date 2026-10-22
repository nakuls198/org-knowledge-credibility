"""Runtime settings (environment variables) and YAML configuration loading."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Environment-driven settings. Secrets are never logged or returned by the API."""

    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env",), env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://okc:okc@localhost:5432/okc"

    # Corpus location (read-only). Points at the Markdown dataset root.
    data_dir: Path = REPO_ROOT / "data" / "sample"
    config_dir: Path = REPO_ROOT / "config"
    evaluation_dir: Path = REPO_ROOT / "evaluation"

    # Search
    search_backend: Literal["elasticsearch", "memory"] = "elasticsearch"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index: str = "okc-sections"
    elasticsearch_timeout_s: float = 20.0

    # Optional ML components
    embeddings_enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    nli_enabled: bool = False
    nli_model: str = "cross-encoder/nli-deberta-v3-base"
    model_cache_dir: Path | None = None

    # Optional LLM (local Ollama). Disabled -> deterministic baseline only.
    llm_provider: Literal["none", "ollama"] = "none"
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "gemma4:e4b"
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 2

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache
def load_config(name: str) -> dict[str, Any]:
    """Load `config/<name>.yaml`. Cached; call `load_config.cache_clear()` after edits."""
    return _load_yaml(get_settings().config_dir / f"{name}.yaml")


def config_hash(cfg: dict[str, Any]) -> str:
    """Stable hash of a configuration snapshot (for audit/reproducibility)."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]
