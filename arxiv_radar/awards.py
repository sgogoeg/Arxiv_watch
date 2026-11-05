"""Daily awards.

Picked from the whole day's arXiv listing (all categories), before the papers are
filtered down to yours:
  longest       longest paper, from the page count authors give in the arXiv comment
  submissions   person with the most papers, ignoring large collaborations
  drama         comments, replies and rebuttals aimed at other papers

Picked from your categories:
  wacky         three "wackiest title" candidates; your votes retrain the scorer
"""
from __future__ import annotations

import json
import logging
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .authors import name_key
from .stats import STOP

log = logging.getLogger(__name__)

# arXiv archives; their RSS feeds together list every new paper of the day.
ALL_ARCHIVES = [
    "astro-ph", "cond-mat", "gr-qc", "hep-ex", "hep-lat", "hep-ph", "hep-th", "math-ph", "nlin", "nucl-ex",
    "nucl-th", "physics", "quant-ph", "math", "cs", "q-bio", "q-fin", "stat", "eess", "econ",
]

MAX_AUTHORS = 30
_COLLAB = re.compile(r"\b(collaboration|consortium|collab\.?|team|group|survey|project)\b", re.I)
_PAGES = re.compile(r"(\d{1,4})(?:\s*\+\s*(\d{1,4}))?\s*(?:pages|pp\b|pgs\b|p\.)", re.I)
_ARXIV_ID = re.compile(r"(?:arXiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_DRAMA_TITLE = re.compile(
    r"^\s*(?:a\s+)?(?:comments?|reply|replies|response|rebuttal|rejoinder|critique|refutation)\b"
    r"|\b(?:comment|reply|response|rebuttal|rejoinder)\s+(?:on|to)\b"
    r"|\bon\s+the\s+(?:claimed|alleged|purported)\b", re.I)
_DRAMA_COMMENT = re.compile(r"\b(?:comment|reply|response|rebuttal)\s+(?:on|to)\b", re.I)
# Explicit "this is a comment/reply" phrasing (strong) vs. critical wording (weak).
_DRAMA_ABSTRACT_STRONG = re.compile(
    r"\b(?:in this (?:comment|reply|rebuttal)|this comment|we comment on|we respond to|we reply to|"
    r"we point out (?:an? |several )?(?:errors?|flaws?|mistakes?))", re.I)
_DRAMA_ABSTRACT = re.compile(
    r"\b(?:recently claimed|claims? (?:made )?(?:in|by) (?:a )?recent|(?:is|are) (?:incorrect|flawed|erroneous|unfounded)|"
    r"contrary to the claims?)", re.I)
_NOT_DRAMA = re.compile(r"\b(erratum|corrigendum|addendum|retraction)\b", re.I)


def _brief(p: dict) -> dict:
    keep = ("id", "title", "authors", "primary_category", "abs_url", "pdf_url", "inspire_url", "comment")
    out = {k: p.get(k) for k in keep if p.get(k)}
    if len(out.get("authors", [])) > 8:
        out["authors"] = out["authors"][:8] + [f"+{len(p['authors']) - 8} more"]
    return out


# ------------------------------------------------------------------ longest paper

def page_count(comment: str | None) -> int | None:
    best = None
    for m in _PAGES.finditer(comment or ""):
        n = int(m.group(1)) + int(m.group(2) or 0)
        if 0 < n <= 2000:
            best = max(best or 0, n)
    return best


def longest(papers: list[dict], n: int = 3) -> dict | None:
    ranked = sorted(((page_count(p.get("comment")), p) for p in papers), key=lambda x: -(x[0] or 0))
    ranked = [(k, p) for k, p in ranked if k]
    if not ranked:
        return None
    return {
        "winner": {**_brief(ranked[0][1]), "pages": ranked[0][0]},
        "runners_up": [{**_brief(p), "pages": k} for k, p in ranked[1:n]],
        "with_page_count": len(ranked),
    }


# ------------------------------------------------------------------ most submissions

def is_collaboration(p: dict) -> bool:
    authors = p.get("authors") or []
    return len(authors) > MAX_AUTHORS or any(_COLLAB.search(a) for a in authors)


def most_submissions(papers: list[dict], max_ties: int = 5) -> dict | None:
    counts: Counter = Counter()
    spellings: dict[str, Counter] = defaultdict(Counter)
    their: dict[str, list[dict]] = defaultdict(list)
    for p in papers:
        if is_collaboration(p):
            continue
        seen = set()
        for a in p.get("authors") or []:
            k = name_key(a)
            if not k or not k.first:
                continue
            key = f"{k.initial} {k.last}"
            if key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            spellings[key][a.strip()] += 1
            their[key].append(p)
    if not counts:
        return None
    top = counts.most_common(1)[0][1]
    if top < 2:
        return None
    winners = [k for k, c in counts.items() if c == top]

    def display(key: str) -> str:  # most complete spelling, then most common
        return max(spellings[key].items(), key=lambda kv: (len(kv[0]), kv[1]))[0]

    winners.sort(key=lambda k: display(k).split()[-1].lower())
    return {
        "count": top,
        "winners": [{"name": display(k), "papers": [_brief(p) for p in their[k]]} for k in winners[:max_ties]],
        "tied": len(winners),
        "excluded_collaborations": sum(1 for p in papers if is_collaboration(p)),
    }


# ------------------------------------------------------------------ arXiv drama

def drama_score(p: dict) -> tuple[int, list[str]]:
    title, comment, abstract = p.get("title", ""), p.get("comment") or "", p.get("abstract", "")
    if _NOT_DRAMA.search(title):
        return 0, []
    signals, score = [], 0
    if _DRAMA_TITLE.search(title):
        score += 3
        signals.append("title")
    if _DRAMA_COMMENT.search(comment):
        score += 2
        signals.append("arXiv comment")
    if _DRAMA_ABSTRACT_STRONG.search(abstract):
        score += 2
        signals.append("abstract")
    elif _DRAMA_ABSTRACT.search(abstract):
        score += 1
        signals.append("critical wording")
    if score and any(m.group(1) != p.get("id") for m in _ARXIV_ID.finditer(f"{title} {comment} {abstract}")):
        score += 1
        signals.append("cites a specific arXiv paper")
    # an abstract phrase alone is too weak
    return (score, signals) if ("title" in signals or "arXiv comment" in signals or score >= 2) else (0, [])


def drama_target(p: dict) -> str | None:
    for m in _ARXIV_ID.finditer(f"{p.get('title', '')} {p.get('comment') or ''} {p.get('abstract', '')}"):
        if m.group(1) != p.get("id"):
            return m.group(1)
    return None


def drama(papers: list[dict], n: int = 3) -> list[dict]:
    hits = []
    for p in papers:
        s, signals = drama_score(p)
        if s:
            t = drama_target(p)
            hits.append((s, {**_brief(p), "signals": signals,
                             "target": {"id": t, "abs_url": f"https://arxiv.org/abs/{t}"} if t else None}))
    hits.sort(key=lambda x: -x[0])
    return [h for _, h in hits[:n]]


# ------------------------------------------------------------------ wackiest title

FEATURES = ["rarity", "title_gap", "question", "exclaim", "catchy_colon", "quotes", "person", "length_extreme",
            "alliteration"]
PRIOR = {"rarity": 1.0, "title_gap": 0.6, "question": 0.4, "exclaim": 0.8, "catchy_colon": 0.4, "quotes": 0.3,
         "person": 0.5, "length_extreme": 0.3, "alliteration": 0.7}
_PERSON = {"you", "your", "we", "our", "i", "my", "me", "us", "let", "lets", "let's"}


def _words(text: str) -> list[str]:
    text = re.sub(r"\$[^$]*\$", " ", text.lower())
    return re.findall(r"[a-z][a-z'\-]+", text)


def raw_features(p: dict, df: Counter, n_docs: int, title_sim: float | None) -> dict:
    title = p.get("title", "")
    words = _words(title)
    content = [w for w in words if w not in STOP and len(w) > 2]
    idf = sorted((math.log((n_docs + 1) / (df.get(w, 0) + 1)) for w in set(content)), reverse=True)
    prefix = title.split(":", 1)[0] if ":" in title else ""
    allit = sum(1 for a, b in zip(content, content[1:]) if a[0] == b[0])
    return {
        "rarity": float(np.mean(idf[:2])) if idf else 0.0,
        "title_gap": 1.0 - title_sim if title_sim is not None else 0.0,
        "question": float("?" in title),
        "exclaim": float("!" in title),
        "catchy_colon": float(bool(prefix) and len(prefix.split()) <= 4),
        "quotes": float(bool(re.search(r"[\"“”]|'[^s\s]", title))),
        "person": float(bool(_PERSON & set(words))),
        "length": float(len(words)),
        "alliteration": float(allit),
    }


def standardize(rows: list[dict]) -> list[dict]:
    """z-score each feature within the day's pool; length becomes distance from typical length."""
    if not rows:
        return []
    out = [dict(r) for r in rows]
    lengths = np.array([r["length"] for r in rows])
    med = float(np.median(lengths))
    for r in out:
        r["length_extreme"] = abs(r.pop("length") - med)
    for f in FEATURES:
        v = np.array([r[f] for r in out], dtype=float)
        sd = v.std() or 1.0
        for r, z in zip(out, (v - v.mean()) / sd):
            r[f] = round(float(z), 4)
    return out


def train_weights(pairs: list[tuple[dict, dict]], prior: dict = PRIOR, l2: float = 1.0,
                  steps: int = 300, lr: float = 0.1) -> dict:
    """Pairwise logistic regression (picked title beats the others), pulled toward the prior."""
    w0 = np.array([prior[f] for f in FEATURES])
    if not pairs:
        return dict(prior)
    X = np.array([[a[f] - b[f] for f in FEATURES] for a, b in pairs])
    w = w0.copy()
    for _ in range(steps):
        p = 1 / (1 + np.exp(-X @ w))
        grad = -X.T @ (1 - p) / len(X) + l2 * (w - w0) / len(X)
        w -= lr * grad
    return {f: round(float(v), 4) for f, v in zip(FEATURES, w)}


def collect_votes(votes: dict, day_docs: dict[str, dict]) -> list[tuple[dict, dict]]:
    """Votes from feedback.json ({date: {"winner": id}}) joined with each day's stored candidate features."""
    pairs = []
    for day, v in votes.items():
        cands = ((day_docs.get(day) or {}).get("awards") or {}).get("wacky", {}).get("candidates", [])
        feats = {c["id"]: c["features"] for c in cands}
        win = v.get("winner")
        if win not in feats:
            continue
        pairs.extend((feats[win], f) for cid, f in feats.items() if cid != win)
    return pairs


def wacky(papers: list[dict], title_sims: list[float | None], corpus: list[str], weights: dict,
          rng: random.Random, explore_from: int = 20) -> dict | None:
    if len(papers) < 3:
        return None
    df: Counter = Counter()
    for doc in corpus:
        df.update(set(_words(doc)))
    rows = standardize([raw_features(p, df, len(corpus), s) for p, s in zip(papers, title_sims)])
    scores = [sum(weights[f] * r[f] for f in FEATURES) for r in rows]
    order = sorted(range(len(papers)), key=lambda i: -scores[i])
    picks = order[:2]
    pool = order[2:explore_from]
    if pool:  # one exploratory candidate so the scorer can learn what it underrates
        picks.append(rng.choice(pool))
    cands = [{**_brief(papers[i]), "features": rows[i], "score": round(scores[i], 3), "explore": k == 2}
             for k, i in enumerate(picks)]
    rng.shuffle(cands)  # don't reveal the ranking
    return {"candidates": cands}


# ------------------------------------------------------------------ all together

def compute(all_papers: list[dict], mine: list[dict], title_sims: list[float | None], weights: dict,
            seed: int = 0) -> dict:
    corpus = [f"{p.get('title', '')} {p.get('abstract', '')}" for p in all_papers]
    return {
        "pool": len(all_papers),
        "longest": longest(all_papers),
        "most_submissions": most_submissions(all_papers),
        "drama": drama(all_papers),
        "wacky": wacky(mine, title_sims, corpus, weights, random.Random(seed)),
    }


def load_weights(store) -> dict:
    model = store.read("awards_model.json") if store.path("awards_model.json").exists() else {}
    return {**PRIOR, **(model.get("weights") or {})}


def retrain(store, writer) -> dict:
    votes = store.read("feedback.json").get("wacky_votes", {})
    docs = {}
    for day in votes:
        doc = store.read_day(day)
        if doc:
            docs[day] = doc
    pairs = collect_votes(votes, docs)
    weights = train_weights(pairs)
    writer.write("awards_model.json", {"weights": weights, "votes": len(votes), "pairs": len(pairs)})
    log.info("wackiest-title scorer trained on %d votes (%d pairs)", len(votes), len(pairs))
    return weights
