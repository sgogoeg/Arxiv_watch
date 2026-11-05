"""Cheap first pass: similarity of each paper to each topic's reference papers.

References per topic: the topic description, its seed papers, and papers saved
into a folder linked to that topic. Keyword hits add a small boost;
negative keywords subtract a penalty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np


@dataclass
class TopicRefs:
    name: str
    ids: list[str] = field(default_factory=list)  # reference ids ("topic:<name>" for the description)
    vecs: np.ndarray | None = None


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    t = text.lower()
    hits = []
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", t):
            hits.append(kw)
    return hits


@dataclass
class PrefilterResult:
    sims: np.ndarray             # n x T adjusted similarity
    raw: np.ndarray              # n x T max cosine before keyword adjustments
    closest: list[dict]          # per paper: {topic: closest reference id}
    keywords: list[dict]         # per paper: {topic: [keywords hit]}


def score(papers: list[dict], paper_vecs: np.ndarray, topics, refs: list[TopicRefs],
          boost: float = 0.05, penalty: float = 0.2) -> PrefilterResult:
    n, T = len(papers), len(topics)
    raw = np.zeros((n, T), dtype=np.float32)
    closest = [dict() for _ in range(n)]
    kws = [dict() for _ in range(n)]
    for j, (topic, ref) in enumerate(zip(topics, refs)):
        if ref.vecs is None or len(ref.ids) == 0 or n == 0:
            continue
        s = paper_vecs @ ref.vecs.T          # n x R cosine (vectors are normalized)
        best = s.argmax(axis=1)
        raw[:, j] = s[np.arange(n), best]
        for i in range(n):
            closest[i][topic.name] = ref.ids[best[i]]
    adj = raw.copy()
    for i, p in enumerate(papers):
        text = f"{p.get('title', '')} {p.get('abstract', '')}"
        for j, topic in enumerate(topics):
            hits = keyword_hits(text, topic.keywords)
            neg = keyword_hits(text, topic.negative_keywords)
            if hits:
                kws[i][topic.name] = hits
            adj[i, j] += min(3, len(hits)) * boost - (penalty if neg else 0.0)
    return PrefilterResult(sims=adj, raw=raw, closest=closest, keywords=kws)


def shortlist(res: PrefilterResult, top_n: int, always: set[int] | None = None) -> list[int]:
    """Indices of the top_n papers by best topic similarity, plus any 'always' indices."""
    if res.sims.shape[0] == 0:
        return []
    best = res.sims.max(axis=1)
    order = list(np.argsort(-best)[:top_n])
    for i in sorted(always or []):
        if i not in order:
            order.append(i)
    return [int(i) for i in order]


def percentile_scores(sims: np.ndarray) -> np.ndarray:
    """Per-topic rank of each paper within the day's batch, in [0, 1].

    Used before a topic has enough training examples: relative, not absolute, relevance.
    """
    n = sims.shape[0]
    if n <= 1:
        return np.ones_like(sims)
    ranks = sims.argsort(axis=0).argsort(axis=0).astype(np.float32)
    return ranks / (n - 1)
