"""Daily analytics for the dashboard's Stats tab.

"general" describes every paper fetched that day. "personal" describes the papers that
passed your filters: a topic score at or above the threshold, or a followed author.
"""
from __future__ import annotations

import re
from collections import Counter

STOP = set("""
a an and are as at be by can for from has have in into is it its of on or our that the their this to via we
with without using based new study towards toward between under over through beyond within than these those
which while not no more most two one three first second all also both case its non how what when where why
do does show shows effect effects role approach analysis results result method methods data paper
probing probe implications revisited revisiting insights novel
""".split())

AUTHOR_BINS = [(1, 1, "1"), (2, 2, "2"), (3, 3, "3"), (4, 5, "4–5"), (6, 10, "6–10"), (11, 50, "11–50"), (51, 10**9, "51+")]


def top_terms(titles: list[str], n: int = 15) -> list[list]:
    """Most common words and two-word phrases in titles (stopwords and LaTeX removed)."""
    c: Counter = Counter()
    for t in titles:
        t = re.sub(r"\$[^$]*\$", " ", t.lower())
        words = [w for w in re.findall(r"[a-z][a-z\-]+", t) if w not in STOP and len(w) > 2]
        c.update(set(words))
        c.update({f"{a} {b}" for a, b in zip(words, words[1:])})
    # drop single words that only appear as part of an equally common phrase
    in_phrase: Counter = Counter()
    for term, k in c.items():
        if " " in term:
            for w in term.split():
                in_phrase[w] = max(in_phrase[w], k)
    out = []
    for term, k in sorted(c.items(), key=lambda x: (-x[1], " " not in x[0], x[0])):
        if k < 2 or len(out) == n:
            break
        if " " not in term and in_phrase[term] >= k:
            continue
        out.append([term, k])
    return out


def _authors_hist(papers: list[dict]) -> list[list]:
    counts = Counter()
    for p in papers:
        n = len(p.get("authors") or [])
        for lo, hi, label in AUTHOR_BINS:
            if lo <= n <= hi:
                counts[label] += 1
                break
    return [[label, counts[label]] for _, _, label in AUTHOR_BINS]


def _base(papers: list[dict], n_cat: int = 12) -> dict:
    cats = Counter(p.get("primary_category") or "(unknown)" for p in papers)
    authors = Counter(a for p in papers for a in (p.get("authors") or []))
    kinds = Counter(p.get("announce_type") or "new" for p in papers)
    return {
        "count": len(papers),
        "new": kinds.get("new", 0),
        "cross": kinds.get("cross", 0),
        "by_category": [[k, v] for k, v in cats.most_common(n_cat)],
        "top_terms": top_terms([p.get("title", "") for p in papers]),
        "top_authors": [[k, v] for k, v in authors.most_common(10) if v > 1],
        "authors_per_paper": _authors_hist(papers),
    }


def similarity_hist(sims: list[float], passed: list[bool], bins: int = 12) -> list[dict]:
    """Histogram of each paper's best similarity to your topics, split by passed / not passed."""
    if not sims:
        return []
    pairs = [(s, ok) for s, ok in zip(sims, passed) if s is not None]
    if not pairs:
        return []
    sims, passed = [s for s, _ in pairs], [ok for _, ok in pairs]
    lo, hi = min(sims), max(sims)
    width = (hi - lo) / bins or 1.0
    out = [{"lo": round(lo + i * width, 3), "hi": round(lo + (i + 1) * width, 3), "passed": 0, "other": 0}
           for i in range(bins)]
    for s, ok in zip(sims, passed):
        i = min(bins - 1, int((s - lo) / width))
        out[i]["passed" if ok else "other"] += 1
    return out


def compute(all_papers: list[dict], best_sim: list[float], entries: list[dict], topics: list[str],
            threshold: float) -> dict:
    """all_papers and best_sim are aligned (every fetched paper); entries are the scored shortlist."""
    personal = [p for p in entries if p.get("topics") or p.get("followed_authors")]
    passed_ids = {p["id"] for p in personal}

    general = _base(all_papers)
    general["similarity_hist"] = similarity_hist(best_sim, [p["id"] in passed_ids for p in all_papers])

    pers = _base(personal)
    pers["by_topic"] = [[t, sum(1 for p in personal if t in p.get("topics", []))] for t in topics]
    pers["followed"] = sum(1 for p in personal if p.get("followed_authors"))
    fetched_by_cat = Counter(p.get("primary_category") or "(unknown)" for p in all_papers)
    passed_by_cat = Counter(p.get("primary_category") or "(unknown)" for p in personal)
    pers["hit_rate_by_category"] = sorted(
        ([c, passed_by_cat.get(c, 0), n] for c, n in fetched_by_cat.items() if n >= 3),
        key=lambda r: (-r[1] / r[2], -r[2]),
    )[:12]
    score_bins = [0] * 10
    for p in entries:
        score_bins[min(9, int(p.get("score", 0) * 10))] += 1
    pers["score_hist"] = [[f"{i / 10:.1f}", n] for i, n in enumerate(score_bins)]
    pers["threshold"] = threshold
    pers["top"] = [{"id": p["id"], "title": p["title"], "score": p.get("score"), "inspire_url": p.get("inspire_url"),
                    "abs_url": p.get("abs_url")} for p in personal[:5]]
    return {"general": general, "personal": pers}
