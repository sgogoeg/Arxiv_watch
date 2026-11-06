"""Daily awards.

Picked from the whole day's arXiv listing (all categories), before the papers are
filtered down to yours:
  longest       longest paper, from the page count authors give in the arXiv comment
  submissions   person with the most papers, ignoring large collaborations
  drama         comments, replies and rebuttals aimed at other papers

Picked from your categories:
  wacky         wackiest title (fixed heuristic score), with runners-up
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

import numpy as np

from .authors import name_key
from .stats import STOP

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

# Fixed weights on per-day z-scores of each feature.
WEIGHTS = {"rarity": 1.0, "question": 0.4, "exclaim": 0.8, "catchy_colon": 0.4, "quotes": 0.3, "person": 0.5,
           "length_extreme": 0.3, "alliteration": 0.7}
FEATURES = list(WEIGHTS)
_PERSON = {"you", "your", "we", "our", "i", "my", "me", "us", "let", "lets", "let's"}


def _words(text: str) -> list[str]:
    text = re.sub(r"\$[^$]*\$", " ", text.lower())
    return re.findall(r"[a-z][a-z'\-]+", text)


def raw_features(p: dict, df: Counter, n_docs: int) -> dict:
    title = p.get("title", "")
    words = _words(title)
    content = [w for w in words if w not in STOP and len(w) > 2]
    idf = sorted((math.log((n_docs + 1) / (df.get(w, 0) + 1)) for w in set(content)), reverse=True)
    prefix = title.split(":", 1)[0] if ":" in title else ""
    allit = sum(1 for a, b in zip(content, content[1:]) if a[0] == b[0])
    return {
        "rarity": float(np.mean(idf[:2])) if idf else 0.0,
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


def wacky(papers: list[dict], corpus: list[str], n: int = 3) -> dict | None:
    """Wackiest title among your papers, with runners-up. Word rarity is measured against the whole day."""
    if len(papers) < n:
        return None
    df: Counter = Counter()
    for doc in corpus:
        df.update(set(_words(doc)))
    rows = standardize([raw_features(p, df, len(corpus)) for p in papers])
    scores = [sum(WEIGHTS[f] * r[f] for f in FEATURES) for r in rows]
    order = sorted(range(len(papers)), key=lambda i: -scores[i])[:n]
    ranked = [{**_brief(papers[i]), "score": round(scores[i], 3)} for i in order]
    return {"winner": ranked[0], "runners_up": ranked[1:]}


# ------------------------------------------------------------------ all together

def compute(all_papers: list[dict], mine: list[dict]) -> dict:
    corpus = [f"{p.get('title', '')} {p.get('abstract', '')}" for p in all_papers]
    return {
        "pool": len(all_papers),
        "longest": longest(all_papers),
        "most_submissions": most_submissions(all_papers),
        "drama": drama(all_papers),
        "wacky": wacky(mine, corpus),
    }
