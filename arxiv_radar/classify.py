"""Topic classifier: SetFit few-shot fine-tuning of the embedding model.

Training data
  positives: topic seed papers, and papers saved into folders. A saved paper is a positive
             for its folder's topic if the folder has one, otherwise for the topics it was
             tagged with when it was saved.
  negatives: deleted papers, plus a random sample of papers that were never shortlisted.

A topic with fewer than `min_positives_per_topic` positives is not trained; it keeps
the similarity-percentile score from the pre-filter. The model is retrained only when
the training set changes (hash check) and is stored in .cache/ (not git).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .embed import paper_text

log = logging.getLogger(__name__)


@dataclass
class Example:
    key: str
    text: str
    labels: set[str] = field(default_factory=set)


def build_training_set(topics, seeds: dict[str, dict], saved, deleted: dict[str, dict],
                       background: list[dict], n_random: int, rng: random.Random | None = None) -> list[Example]:
    rng = rng or random.Random(0)
    names = {t.name for t in topics}
    ex: dict[str, Example] = {}

    def add(key: str, paper: dict, labels: set[str]):
        text = paper_text(paper)
        if not text or text == "[SEP]":
            return
        if key in ex:
            ex[key].labels |= labels
        else:
            ex[key] = Example(key, text, set(labels))

    for t in topics:
        for sid in t.seed_ids:
            if sid in seeds:
                add(sid, seeds[sid], {t.name})
    for paper, folder_topic, _folder in saved:
        if folder_topic and folder_topic in names:
            labels = {folder_topic}
        else:
            labels = {x for x in paper.get("topics", []) if x in names}
        if labels:
            add(paper["id"], paper, labels)
    for pid, paper in deleted.items():
        if pid not in ex:
            add(pid, {**paper, "id": pid}, set())
    pool = [p for p in background if p["id"] not in ex]
    rng.shuffle(pool)
    for p in pool[:n_random]:
        add(p["id"], p, set())
    return list(ex.values())


def positives_per_topic(examples: list[Example], topic_names: list[str]) -> dict[str, int]:
    return {t: sum(t in e.labels for e in examples) for t in topic_names}


def training_hash(examples: list[Example], topics: list[str], model: str) -> str:
    payload = json.dumps(
        {"model": model, "topics": topics, "ex": sorted((e.key, sorted(e.labels)) for e in examples)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def training_steps(n_examples: int, cfg) -> int:
    """Steps for one pass over SetFit's pairs (2 per example per iteration), capped at cfg.max_steps.

    SetFit's max_steps overrides the epoch count, so a larger value would cycle over the same pairs again.
    """
    one_pass = math.ceil(2 * cfg.num_iterations * n_examples / cfg.batch_size)
    return max(1, min(cfg.max_steps, one_pass))


def _time_budget(seconds: float):
    from transformers import TrainerCallback

    class TimeBudget(TrainerCallback):
        def on_train_begin(self, args, state, control, **kw):
            self.start = time.monotonic()

        def on_step_end(self, args, state, control, **kw):
            if time.monotonic() - self.start > seconds:
                log.warning("SetFit time budget (%ds) reached after %d/%d steps; using the model as trained so far",
                            seconds, state.global_step, state.max_steps)
                control.should_training_stop = True

    return TimeBudget()


class SetFitTopicClassifier:
    def __init__(self, model_name: str, model_dir: str | Path, cfg):
        self.model_name = model_name
        self.dir = Path(model_dir)
        self.cfg = cfg
        self.model = None
        self.topics: list[str] = []

    def _meta_path(self) -> Path:
        return self.dir / "radar_meta.json"

    def load_if_current(self, h: str) -> bool:
        meta = self._meta_path()
        if not meta.exists():
            return False
        m = json.loads(meta.read_text())
        if m.get("hash") != h:
            return False
        from setfit import SetFitModel

        self.model = SetFitModel.from_pretrained(str(self.dir))
        self.topics = m["topics"]
        return True

    def train(self, examples: list[Example], topics: list[str], h: str) -> None:
        from datasets import Dataset
        from setfit import SetFitModel, Trainer, TrainingArguments

        log.info("training SetFit on %d examples for topics %s", len(examples), topics)
        single = len(topics) == 1  # one topic: plain binary head; several: one-vs-rest multi-label head
        if single:
            labels = [1 if topics[0] in e.labels else 0 for e in examples]
            model = SetFitModel.from_pretrained(self.model_name)
        else:
            labels = [[1 if t in e.labels else 0 for t in topics] for e in examples]
            model = SetFitModel.from_pretrained(self.model_name, multi_target_strategy="one-vs-rest")
        model.model_body.max_seq_length = self.cfg.max_seq_length
        ds = Dataset.from_dict({"text": [e.text for e in examples], "label": labels})
        steps = training_steps(len(examples), self.cfg)
        args = TrainingArguments(
            output_dir=str(self.dir.parent / "setfit-checkpoints"),
            save_strategy="no",
            report_to="none",
            batch_size=self.cfg.batch_size,
            num_iterations=self.cfg.num_iterations,
            num_epochs=1,
            max_steps=steps,
            seed=0,
        )
        log.info("SetFit: %d steps, batch %d, %d tokens, time budget %ds",
                 steps, self.cfg.batch_size, self.cfg.max_seq_length, self.cfg.time_budget_seconds)
        start = time.monotonic()
        Trainer(model=model, args=args, train_dataset=ds,
                callbacks=[_time_budget(self.cfg.time_budget_seconds)]).train()
        log.info("SetFit trained in %.0fs", time.monotonic() - start)
        self.dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(self.dir))
        self._meta_path().write_text(json.dumps({"hash": h, "topics": topics}))
        self.model, self.topics = model, topics

    def predict(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, len(self.topics)), dtype=np.float32)
        proba = self.model.predict_proba(texts, batch_size=self.cfg.batch_size)
        proba = np.asarray(proba.cpu() if hasattr(proba, "cpu") else proba, dtype=np.float32)
        if proba.ndim == 1:
            proba = proba[:, None]
        if len(self.topics) == 1 and proba.shape[1] == 2:  # binary head: keep P(positive)
            proba = proba[:, 1:]
        return proba


def classify(cfg, topics, examples: list[Example], texts: list[str], fallback: np.ndarray,
             model_dir: str | Path) -> tuple[np.ndarray, list[str]]:
    """Return (n x T scores in [0, 1], per-topic score source: 'model' or 'similarity')."""
    names = [t.name for t in topics]
    scores = fallback.astype(np.float32).copy()
    sources = ["similarity"] * len(names)
    if cfg.classifier.backend != "setfit" or cfg.embedding.backend == "hashing":
        return scores, sources  # SetFit fine-tunes a transformer; the hashing embedder has none
    counts = positives_per_topic(examples, names)
    trainable = [t for t in names if counts[t] >= cfg.classifier.min_positives_per_topic]
    n_neg = sum(1 for e in examples if not e.labels)
    if not trainable or n_neg == 0:
        log.info("not enough labels to train yet (positives per topic: %s, negatives: %d)", counts, n_neg)
        return scores, sources
    try:
        clf = SetFitTopicClassifier(cfg.embedding.model, model_dir, cfg.classifier)
        h = training_hash(examples, trainable, cfg.embedding.model)
        if not clf.load_if_current(h):
            clf.train(examples, trainable, h)
        proba = clf.predict(texts)
    except ImportError:
        log.warning("setfit is not installed; using similarity scores (pip install '.[ml]')")
        return scores, sources
    for k, t in enumerate(clf.topics):
        j = names.index(t)
        scores[:, j] = proba[:, k]
        sources[j] = "model"
    return scores, sources
