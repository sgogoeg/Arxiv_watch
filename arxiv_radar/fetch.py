"""Fetch the day's new arXiv papers.

1. One RSS request per category (rss.arxiv.org) gives the IDs announced today,
   with announce type new / cross / replace / replace-cross. Replacements are dropped.
2. Batched arXiv API calls (export.arxiv.org, id_list) fill in full metadata.
   If the API fails, the RSS fields are used as they are.

arXiv asks for at most one request every 3 seconds over a single connection,
so every request goes through one shared RateLimiter.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

import feedparser
import httpx

log = logging.getLogger(__name__)

RSS_URL = "https://rss.arxiv.org/rss/{cat}"
API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "arxiv-radar/0.1 (personal paper feed)"
KEEP_ANNOUNCE = {"new", "cross"}
_ID_RE = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")


def normalize_id(raw: str) -> str:
    """'oai:arXiv.org:2511.01234v2', 'http://arxiv.org/abs/2511.01234v1', 'arXiv:2511.01234' -> '2511.01234'."""
    raw = raw.strip()
    raw = re.sub(r"^(oai:arXiv\.org:|arxiv:|https?://(export\.)?arxiv\.org/(abs|pdf)/)", "", raw, flags=re.I)
    raw = raw.removesuffix(".pdf")
    m = _ID_RE.search(raw)
    return m.group(1) if m else raw


class RateLimiter:
    def __init__(self, min_interval: float = 3.0, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            delay = self.min_interval - (now - self._last)
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
        self._last = now


class ArxivClient:
    """Single-connection client shared by every arXiv request in a run."""

    def __init__(self, min_interval: float = 3.0, retries: int = 4, client: httpx.Client | None = None):
        self.limiter = RateLimiter(min_interval)
        self.retries = retries
        self.http = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=60,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=1),
        )

    def get(self, url: str, params: dict | None = None) -> str:
        backoff = 5.0
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                r = self.http.get(url, params=params)
                if r.status_code == 200:
                    return r.text
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
                log.warning("arXiv %s returned %s (attempt %d)", url, r.status_code, attempt + 1)
            except httpx.TransportError as e:
                log.warning("arXiv %s failed: %s (attempt %d)", url, e, attempt + 1)
            if attempt < self.retries:
                time.sleep(backoff)
                backoff *= 2
        raise RuntimeError(f"arXiv request failed after {self.retries + 1} attempts: {url}")


# --------------------------------------------------------------------------- parsing

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_rss(xml: str) -> tuple[list[dict], date | None]:
    """Parse an arXiv RSS feed. Returns (papers kept, announcement date)."""
    feed = feedparser.parse(xml)
    announced: date | None = None
    pub = feed.feed.get("published") or feed.feed.get("updated")
    if pub:
        try:
            announced = parsedate_to_datetime(pub).date()
        except (TypeError, ValueError):
            announced = None
    papers = []
    for e in feed.entries:
        kind = (e.get("arxiv_announce_type") or "new").strip().lower()
        if kind not in KEEP_ANNOUNCE:
            continue
        pid = normalize_id(e.get("id") or e.get("link", ""))
        desc = e.get("summary", "")
        abstract = desc.split("Abstract:", 1)[1] if "Abstract:" in desc else desc
        creators = e.get("author") or e.get("dc_creator") or ""
        authors = [a.strip() for a in re.split(r",\s*|\s+and\s+", creators) if a.strip()]
        cats = [t["term"] for t in e.get("tags", []) if t.get("term")]
        papers.append(
            {
                "id": pid,
                "title": _clean(e.get("title", "")),
                "authors": authors,
                "abstract": _clean(abstract),
                "categories": cats,
                "primary_category": cats[0] if cats else None,
                "announce_type": kind,
                "abs_url": f"https://arxiv.org/abs/{pid}",
                "pdf_url": f"https://arxiv.org/pdf/{pid}",
            }
        )
    return papers, announced


def parse_api(xml: str) -> list[dict]:
    """Parse an arXiv API Atom response into paper dicts."""
    feed = feedparser.parse(xml)
    out = []
    for e in feed.entries:
        if "id" not in e or "arxiv.org/api/errors" in e.get("id", ""):
            continue
        pid = normalize_id(e["id"])
        cats = [t["term"] for t in e.get("tags", []) if t.get("term")]
        prim = (e.get("arxiv_primary_category") or {}).get("term") or (cats[0] if cats else None)
        pdf = next((l["href"] for l in e.get("links", []) if l.get("title") == "pdf"), f"https://arxiv.org/pdf/{pid}")
        out.append(
            {
                "id": pid,
                "title": _clean(e.get("title", "")),
                "authors": [a.get("name", "").strip() for a in e.get("authors", []) if a.get("name")],
                "abstract": _clean(e.get("summary", "")),
                "categories": cats,
                "primary_category": prim,
                "published": e.get("published"),
                "updated": e.get("updated"),
                "doi": e.get("arxiv_doi"),
                "comment": _clean(e.get("arxiv_comment", "")) or None,
                "abs_url": f"https://arxiv.org/abs/{pid}",
                "pdf_url": pdf.replace("http://", "https://"),
            }
        )
    return out


def merge_papers(rss: Iterable[dict], api: Iterable[dict]) -> list[dict]:
    """Dedupe RSS papers (a paper appears in each category it is listed in) and overlay API metadata."""
    by_id: dict[str, dict] = {}
    for p in rss:
        if p["id"] in by_id:
            # keep "new" over "cross" so the paper is shown under its own listing
            if p["announce_type"] == "new":
                by_id[p["id"]]["announce_type"] = "new"
            continue
        by_id[p["id"]] = dict(p)
    for a in api:
        if a["id"] in by_id:
            kind = by_id[a["id"]].get("announce_type")
            by_id[a["id"]].update({k: v for k, v in a.items() if v})
            by_id[a["id"]]["announce_type"] = kind
    return list(by_id.values())


# --------------------------------------------------------------------------- high level

def fetch_metadata(client: ArxivClient, ids: list[str], batch: int = 100) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        xml = client.get(API_URL, {"id_list": ",".join(chunk), "max_results": len(chunk)})
        out.extend(parse_api(xml))
    return out


def fetch_today(categories: list[str], client: ArxivClient | None = None) -> tuple[list[dict], date]:
    client = client or ArxivClient()
    rss: list[dict] = []
    announced: date | None = None
    failed = []
    for cat in categories:
        try:
            papers, d = parse_rss(client.get(RSS_URL.format(cat=cat)))
        except Exception as e:  # one bad or unreachable category should not sink the day
            log.warning("%s: could not fetch the RSS feed (%s); skipping", cat, e)
            failed.append(cat)
            continue
        log.info("%s: %d new/cross-listed papers", cat, len(papers))
        rss.extend(papers)
        announced = announced or d
    if failed and len(failed) == len(categories):
        raise RuntimeError(f"no arXiv category could be fetched: {failed}")
    ids = list(dict.fromkeys(p["id"] for p in rss))
    try:
        api = fetch_metadata(client, ids)
    except Exception as e:  # metadata is a bonus; RSS already has title/authors/abstract
        log.warning("arXiv API metadata failed, using RSS fields only: %s", e)
        api = []
    return merge_papers(rss, api), announced or datetime.now(timezone.utc).date()


def search_author(client: ArxivClient, name: str, max_results: int = 50) -> list[dict]:
    """Recent papers by an author (arXiv API au: query), newest first."""
    xml = client.get(
        API_URL,
        {
            "search_query": f'au:"{name}"',
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results,
        },
    )
    return parse_api(xml)


def load_offline(fixture_dir: str | Path) -> tuple[list[dict], date]:
    """Read a recorded day: <dir>/rss/*.xml plus optional <dir>/api/*.xml."""
    d = Path(fixture_dir)
    rss: list[dict] = []
    announced = None
    for f in sorted((d / "rss").glob("*.xml")):
        papers, day = parse_rss(f.read_text())
        rss.extend(papers)
        announced = announced or day
    api: list[dict] = []
    for f in sorted((d / "api").glob("*.xml")):
        api.extend(parse_api(f.read_text()))
    return merge_papers(rss, api), announced or datetime.now(timezone.utc).date()
