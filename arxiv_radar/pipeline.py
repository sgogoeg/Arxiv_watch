"""Daily run: fetch → match authors → embed → pre-filter → classify → summarize → write."""
from __future__ import annotations

import json
import logging
import random
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from . import prefilter
from .authors import AuthorMatcher, backfill, update_matches
from .citations import update_citations
from .classify import build_training_set, classify
from .config import Config
from .embed import EmbeddingCache, make_embedder, paper_text
from .fetch import ArxivClient, fetch_metadata, fetch_today, load_offline, parse_api
from .store import DEFAULTS, Store
from .summarize import summarize

log = logging.getLogger(__name__)

BACKGROUND_SAMPLE = 150   # random non-shortlisted papers kept per day as training negatives
BACKGROUND_DAYS = 14


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_seeds(cfg: Config, store: Store, client: ArxivClient | None, offline: Path | None) -> dict[str, dict]:
    seeds = store.read("seeds.json") if store.path("seeds.json").exists() else {}
    wanted = {sid for t in cfg.topics for sid in t.seed_ids}
    missing = sorted(wanted - set(seeds))
    if missing and offline is not None:
        for f in sorted((offline / "seeds").glob("*.xml")):
            for p in parse_api(f.read_text()):
                if p["id"] in wanted:
                    seeds[p["id"]] = p
    elif missing and client is not None:
        try:
            for p in fetch_metadata(client, missing):
                seeds[p["id"]] = p
        except Exception as e:
            log.warning("could not fetch seed papers %s: %s", missing, e)
    still = sorted(wanted - set(seeds))
    if still:
        log.warning("seed papers not found (check the IDs): %s", still)
    return seeds


def topic_refs(cfg: Config, seeds: dict, saved, cache: EmbeddingCache, embedder) -> list[prefilter.TopicRefs]:
    refs = []
    for t in cfg.topics:
        items = [(f"topic:{t.name}:{t.description}", f"{t.name}. {t.description}".strip())]
        ids = [f"topic:{t.name}"]
        for sid in t.seed_ids:
            if sid in seeds:
                items.append((sid, paper_text(seeds[sid])))
                ids.append(sid)
        for paper, folder_topic, _ in saved:
            linked = folder_topic == t.name or (not folder_topic and t.name in paper.get("topics", []))
            if linked and paper["id"] not in ids:
                items.append((paper["id"], paper_text(paper)))
                ids.append(paper["id"])
        refs.append(prefilter.TopicRefs(t.name, ids, cache.embed(embedder, items)))
    return refs


def recent_background(store: Store, days: int = BACKGROUND_DAYS, exclude: str | None = None) -> list[dict]:
    """Never-shortlisted papers from recent days (training negatives), excluding the day being scored."""
    out = []
    files = [f for f in store.day_files() if f.stem != exclude]
    for f in files[-days:]:
        out.extend(json.loads(f.read_text()).get("background", []))
    return out


def update_index(store: Store, writer: Store, cfg: Config, day: str, count: int, fetched: int) -> None:
    idx = store.read("index.json")
    days = [d for d in idx.get("days", []) if d["date"] != day]
    days.append({"date": day, "count": count, "fetched": fetched})
    days.sort(key=lambda d: d["date"], reverse=True)
    writer.write("index.json", {
        "days": days,
        "topics": cfg.topic_names,
        "threshold": cfg.score_threshold,
        "generated": _now(),
    })


def run(cfg: Config, *, day: str | None = None, offline: str | None = None, out_dir: str | None = None,
        skip_citations: bool = False, seed: int = 0) -> dict:
    store = Store(cfg.data_dir)
    writer = Store(out_dir) if out_dir else store
    offline_dir = Path(offline) if offline else None
    client = None if offline_dir else ArxivClient()

    # 1. fetch
    if offline_dir:
        papers, announced = load_offline(offline_dir)
    else:
        papers, announced = fetch_today(cfg.categories, client)
    day = day or announced.isoformat()
    log.info("%s: %d papers fetched", day, len(papers))

    deleted = store.deleted_papers()
    saved = store.saved_papers()
    saved_where: dict[str, list[str]] = {}
    for paper, _, folder in saved:
        saved_where.setdefault(paper["id"], []).append(folder)
    papers = [p for p in papers if p["id"] not in deleted]

    followed = store.followed_author_names(cfg.authors)
    if not papers:
        log.warning("no new papers for %s (weekend/holiday or empty categories); nothing to rank", day)
        _finish_side_tasks(cfg, store, writer, client, followed, [], day, skip_citations or bool(offline_dir))
        return {"date": day, "papers": []}

    # 2. followed authors
    matcher = AuthorMatcher(followed)
    for p in papers:
        p["followed_authors"] = matcher.match(p)

    # 3. embeddings + pre-filter
    seeds = ensure_seeds(cfg, store, client, offline_dir)
    if seeds and (not offline_dir or store is writer):
        writer.write("seeds.json", seeds)
    embedder = make_embedder(cfg.embedding)
    cache = EmbeddingCache(Path(cfg.cache_dir) / "embeddings.npz", getattr(embedder, "name", "hashing"))
    refs = topic_refs(cfg, seeds, saved, cache, embedder)
    vecs = embedder.encode([paper_text(p) for p in papers])
    pre = prefilter.score(papers, vecs, cfg.topics, refs, cfg.keyword_boost, cfg.negative_keyword_penalty)
    always = {i for i, p in enumerate(papers) if p["followed_authors"]}
    idx = prefilter.shortlist(pre, cfg.prefilter_top_n, always)
    fallback = prefilter.percentile_scores(pre.sims)

    # 4. classifier
    rng = random.Random(seed)
    examples = build_training_set(cfg.topics, seeds, saved, deleted, recent_background(store, exclude=day),
                                  cfg.classifier.random_negatives, rng)
    texts = [paper_text(papers[i]) for i in idx]
    scores, sources = classify(cfg, cfg.topics, examples, texts, fallback[idx], Path(cfg.cache_dir) / "model")
    adjust = (pre.sims - pre.raw)[idx]
    scores = np.clip(scores + adjust, 0.0, 1.0)

    # 5. assemble
    names = cfg.topic_names
    ref_titles = {sid: s.get("title") for sid, s in seeds.items()}
    ref_titles.update({p["id"]: p.get("title") for p, _, _ in saved})
    entries = []
    for row, i in enumerate(idx):
        p = dict(papers[i])
        s = {t: round(float(scores[row, j]), 3) for j, t in enumerate(names)}
        best = max(s, key=s.get)
        closest = pre.closest[i].get(best)
        p.update({
            "date": day,
            "scores": s,
            "score": s[best],
            "best_topic": best,
            "topics": [t for t in names if s[t] >= cfg.score_threshold],
            "why": {
                "keywords": pre.keywords[i],
                "closest": None if not closest or closest.startswith("topic:") else
                {"id": closest, "title": ref_titles.get(closest)},
                "similarity": round(float(pre.raw[i, names.index(best)]), 3),
            },
        })
        if p["id"] in saved_where:
            p["saved_in"] = saved_where[p["id"]]
        entries.append(p)
    entries.sort(key=lambda p: (p["score"], bool(p["followed_authors"])), reverse=True)

    # 6. summaries for the top papers
    summarize(entries, cfg.summary)

    # 7. write
    short = set(idx)
    rest = [i for i in range(len(papers)) if i not in short]
    rng.shuffle(rest)
    background = [{k: papers[i].get(k) for k in ("id", "title", "abstract")} for i in rest[:BACKGROUND_SAMPLE]]
    day_doc = {
        "date": day,
        "generated": _now(),
        "categories": cfg.categories,
        "topics": names,
        "score_source": dict(zip(names, sources)),
        "counts": {"fetched": len(papers), "shortlisted": len(entries)},
        "papers": entries,
        "background": background,
    }
    writer.write_day(day, day_doc)
    update_index(store, writer, cfg, day, len(entries), len(papers))

    _finish_side_tasks(cfg, store, writer, client, followed, [p for p in papers if p["followed_authors"]],
                       day, skip_citations or bool(offline_dir))
    cache.save()
    log.info("wrote %s with %d papers (sources: %s)", day, len(entries), dict(zip(names, sources)))
    return day_doc


def _finish_side_tasks(cfg: Config, store: Store, writer: Store, client, followed: list[str],
                       author_papers: list[dict], day: str, skip_citations: bool) -> None:
    matches = store.read("author_matches.json")
    update_matches(matches, author_papers, day)
    if client:
        backfill(client, followed, matches, date.fromisoformat(day), cfg.author_backfill_days)
    writer.write("author_matches.json", matches)
    if cfg.citations.enabled and not skip_citations:
        refresh_citations(cfg, store, writer, day)


def refresh_citations(cfg: Config, store: Store, writer: Store, day: str | None = None) -> dict:
    day = day or datetime.now(timezone.utc).date().isoformat()
    cites = update_citations(store.read("my_papers.json"), store.read("citations.json"), day,
                             cfg.citations.max_citing_works)
    writer.write("citations.json", cites)
    return cites


def refresh(cfg: Config) -> None:
    """Light update after dashboard edits: backfill new followed authors and refresh citations."""
    store = Store(cfg.data_dir)
    client = ArxivClient()
    matches = store.read("author_matches.json")
    followed = store.followed_author_names(cfg.authors)
    today = datetime.now(timezone.utc).date()
    backfill(client, followed, matches, today, cfg.author_backfill_days)
    store.write("author_matches.json", matches)
    if cfg.citations.enabled:
        refresh_citations(cfg, store, store)


def retrain(cfg: Config) -> None:
    """Train (or reuse) the classifier without fetching new papers."""
    store = Store(cfg.data_dir)
    seeds = ensure_seeds(cfg, store, ArxivClient(), None)
    examples = build_training_set(cfg.topics, seeds, store.saved_papers(), store.deleted_papers(),
                                  recent_background(store), cfg.classifier.random_negatives, random.Random(0))
    dummy = np.zeros((0, len(cfg.topics)), dtype=np.float32)
    _, sources = classify(cfg, cfg.topics, examples, [], dummy, Path(cfg.cache_dir) / "model")
    log.info("classifier status per topic: %s", dict(zip(cfg.topic_names, sources)))


def build_site(cfg: Config, site_src: str = "site", out: str = "_site") -> Path:
    out_p = Path(out)
    if out_p.exists():
        shutil.rmtree(out_p)
    shutil.copytree(site_src, out_p)
    data = Path(cfg.data_dir)
    if data.exists():
        shutil.copytree(data, out_p / "data", ignore=shutil.ignore_patterns("*.tmp"))
    # Empty defaults so the dashboard never requests a missing file.
    for name, default in DEFAULTS.items():
        target = out_p / "data" / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(default))
    (out_p / "config.json").write_text(json.dumps({
        "repo": cfg.repo,
        "branch": cfg.github_branch,
        "data_dir": cfg.data_dir,
        "topics": cfg.topic_names,
        "threshold": cfg.score_threshold,
    }, indent=1))
    (out_p / ".nojekyll").write_text("")
    return out_p
