"""Configuration loaded from config.yaml (see config.example.yaml)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class Topic(BaseModel):
    name: str
    description: str = ""
    seed_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)


    @field_validator("seed_ids", mode="before")
    @classmethod
    def _strip_versions(cls, v):
        from .fetch import normalize_id

        return [normalize_id(str(x)) for x in (v or [])]


class EmbeddingConfig(BaseModel):
    # "sentence-transformers" (SPECTER2 by default) or "hashing" (no download, for tests/dry runs)
    backend: str = "sentence-transformers"
    model: str = "allenai/specter2_base"
    batch_size: int = 32


class ClassifierConfig(BaseModel):
    # "setfit" (fine-tuned local model) or "similarity" (no training, seed similarity only)
    backend: str = "setfit"
    min_positives_per_topic: int = 3
    random_negatives: int = 100
    num_iterations: int = 10
    max_steps: int = 400
    batch_size: int = 16


class SummaryConfig(BaseModel):
    # "flan-t5" (local model), "first-sentence" or "none"
    backend: str = "flan-t5"
    model: str = "google/flan-t5-base"
    top_k: int = 20
    time_budget_seconds: int = 600


class CitationsConfig(BaseModel):
    enabled: bool = True
    max_citing_works: int = 200


class InspireConfig(BaseModel):
    enabled: bool = True
    recheck_days: int = 3  # re-check recent days for papers that were not in INSPIRE yet


class Config(BaseModel):
    categories: list[str]
    topics: list[Topic]
    authors: list[str] = Field(default_factory=list)
    prefilter_top_n: int = 150
    score_threshold: float = 0.5
    keyword_boost: float = 0.05
    negative_keyword_penalty: float = 0.2
    author_backfill_days: int = 30
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    citations: CitationsConfig = Field(default_factory=CitationsConfig)
    inspire: InspireConfig = Field(default_factory=InspireConfig)
    # "owner/repo" and branch the dashboard commits edits to. Defaults to $GITHUB_REPOSITORY.
    github_repo: str | None = None
    github_branch: str = "main"
    data_dir: str = "data"
    cache_dir: str = ".cache"

    @field_validator("categories", mode="before")
    @classmethod
    def _need_categories(cls, v) -> list[str]:
        v = [str(c).strip() for c in (v or []) if c and str(c).strip()]
        if not v:
            raise ValueError("config: 'categories' must list at least one arXiv category, e.g. cs.LG")
        return v

    @field_validator("topics", mode="before")
    @classmethod
    def _topics_list(cls, v):
        return v or []

    @field_validator("topics")
    @classmethod
    def _need_topics(cls, v: list[Topic]) -> list[Topic]:
        if not v:
            raise ValueError("config: define at least one topic")
        names = [t.name for t in v]
        if len(set(names)) != len(names):
            raise ValueError("config: topic names must be unique")
        return v

    @property
    def topic_names(self) -> list[str]:
        return [t.name for t in self.topics]

    @property
    def repo(self) -> str | None:
        return self.github_repo or os.environ.get("GITHUB_REPOSITORY")


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Copy config.example.yaml to config.yaml and edit it.")
    raw = yaml.safe_load(path.read_text()) or {}
    return Config.model_validate(raw)
