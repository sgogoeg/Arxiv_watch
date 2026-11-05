"""Citation tracker for the user's own papers ("My papers" tab).

Each entry in data/my_papers.json is an arXiv ID, a DOI, or a title. It is resolved
once to an OpenAlex work (cached in citations.json). Each run then records the citation
count history and the list of citing works. Semantic Scholar is used as a fallback when
OpenAlex cannot resolve a paper and S2_API_KEY is set.
"""
from __future__ import annotations

import logging
import os
import re
import time

import httpx

from .fetch import USER_AGENT, normalize_id

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org/graph/v1"
_ARXIV_RE = re.compile(r"^(arxiv:)?\s*(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)


def classify_query(q: str) -> tuple[str, str]:
    q = q.strip()
    q2 = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", q, flags=re.I)
    if _ARXIV_RE.match(q2):
        return "arxiv", normalize_id(q2)
    m = re.search(r"(10\.\d{4,9}/\S+)", q)
    if m:
        return "doi", m.group(1).rstrip(".")
    return "title", q


class Http:
    def __init__(self, client: httpx.Client | None = None, min_interval: float = 0.2):
        self.http = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
        self.min_interval = min_interval
        self._last = 0.0

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict | None:
        backoff = 2.0
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.http.get(url, params=params, headers=headers)
            except httpx.TransportError as e:
                log.warning("%s failed: %s", url, e)
            else:
                if r.status_code == 404:
                    return None
                if r.status_code == 200:
                    return r.json()
                log.warning("%s returned %s", url, r.status_code)
                if r.status_code not in (429, 500, 502, 503, 504):
                    return None
            time.sleep(backoff)
            backoff *= 2
        return None


# --------------------------------------------------------------------------- OpenAlex

def _oa_params(extra: dict | None = None) -> dict:
    p = dict(extra or {})
    mail = os.environ.get("OPENALEX_MAILTO")
    if mail:
        p["mailto"] = mail
    return p


def _oa_work_summary(w: dict) -> dict:
    return {
        "source": "openalex",
        "id": w["id"].rsplit("/", 1)[-1],
        "title": w.get("display_name") or w.get("title"),
        "year": w.get("publication_year"),
        "url": w.get("doi") or w["id"],
        "count": w.get("cited_by_count", 0),
    }


def openalex_resolve(http: Http, kind: str, value: str) -> dict | None:
    if kind == "arxiv":
        w = http.get_json(f"{OPENALEX}/works/doi:10.48550/arXiv.{value}", _oa_params())
        if w:
            return _oa_work_summary(w)
        return None
    if kind == "doi":
        w = http.get_json(f"{OPENALEX}/works/doi:{value}", _oa_params())
        return _oa_work_summary(w) if w else None
    res = http.get_json(f"{OPENALEX}/works", _oa_params({"search": value, "per-page": 1}))
    if res and res.get("results"):
        return _oa_work_summary(res["results"][0])
    return None


def openalex_refresh(http: Http, work_id: str, max_citing: int) -> tuple[int, list[dict]] | None:
    w = http.get_json(f"{OPENALEX}/works/{work_id}", _oa_params({"select": "id,cited_by_count"}))
    if not w:
        return None
    citing: list[dict] = []
    cursor = "*"
    while cursor and len(citing) < max_citing:
        res = http.get_json(
            f"{OPENALEX}/works",
            _oa_params({
                "filter": f"cites:{work_id}",
                "per-page": min(200, max_citing),
                "cursor": cursor,
                "sort": "publication_date:desc",
                "select": "id,display_name,publication_year,publication_date,doi,authorships",
            }),
        )
        if not res:
            break
        for c in res.get("results", []):
            citing.append({
                "id": c["id"].rsplit("/", 1)[-1],
                "title": c.get("display_name"),
                "year": c.get("publication_year"),
                "date": c.get("publication_date"),
                "authors": [a["author"]["display_name"] for a in c.get("authorships", [])[:8] if a.get("author")],
                "url": c.get("doi") or c["id"],
            })
        cursor = (res.get("meta") or {}).get("next_cursor")
        if not res.get("results"):
            break
    return w.get("cited_by_count", 0), citing[:max_citing]


# --------------------------------------------------------------------------- Semantic Scholar

def s2_lookup(http: Http, kind: str, value: str, max_citing: int) -> tuple[dict, list[dict]] | None:
    key = os.environ.get("S2_API_KEY")
    if not key:
        return None
    headers = {"x-api-key": key}
    fields = "title,year,citationCount,externalIds,url"
    if kind == "title":
        res = http.get_json(f"{S2}/paper/search", {"query": value, "limit": 1, "fields": fields}, headers)
        paper = (res or {}).get("data", [None])[0] if res else None
    else:
        pid = f"arXiv:{value}" if kind == "arxiv" else f"DOI:{value}"
        paper = http.get_json(f"{S2}/paper/{pid}", {"fields": fields}, headers)
    if not paper:
        return None
    res = http.get_json(
        f"{S2}/paper/{paper['paperId']}/citations",
        {"fields": "title,year,authors,url,publicationDate", "limit": min(1000, max_citing)},
        headers,
    )
    citing = []
    for c in (res or {}).get("data", []):
        cp = c.get("citingPaper") or {}
        citing.append({
            "id": cp.get("paperId"),
            "title": cp.get("title"),
            "year": cp.get("year"),
            "date": cp.get("publicationDate"),
            "authors": [a.get("name") for a in (cp.get("authors") or [])[:8]],
            "url": cp.get("url"),
        })
    summary = {
        "source": "semanticscholar",
        "id": paper["paperId"],
        "title": paper.get("title"),
        "year": paper.get("year"),
        "url": paper.get("url"),
        "count": paper.get("citationCount", 0),
    }
    return summary, citing


# --------------------------------------------------------------------------- update

def record(entry: dict, today: str, count: int, citing: list[dict]) -> dict:
    hist = entry.setdefault("history", [])
    if hist and hist[-1]["date"] == today:
        hist[-1]["count"] = count
    else:
        hist.append({"date": today, "count": count})
    seen = {c["id"]: c.get("first_seen") for c in entry.get("citing", [])}
    for c in citing:
        c["first_seen"] = seen.get(c["id"]) or today
    entry["count"] = count
    entry["citing"] = citing
    entry["checked"] = today
    entry.pop("error", None)
    return entry


def update_citations(my_papers: dict, citations: dict, today: str, max_citing: int = 200,
                     http: Http | None = None) -> dict:
    http = http or Http()
    papers = citations.setdefault("papers", {})
    wanted = {p["query"]: p for p in my_papers.get("papers", []) if p.get("query")}
    for q in list(papers):
        if q not in wanted:
            del papers[q]
    for q, item in wanted.items():
        entry = papers.setdefault(q, {"query": q})
        entry["label"] = item.get("label")
        kind, value = classify_query(q)
        try:
            resolved = entry.get("resolved")
            if not resolved:
                resolved = openalex_resolve(http, kind, value)
                if resolved:
                    entry["resolved"] = resolved
            if resolved and resolved["source"] == "openalex":
                got = openalex_refresh(http, resolved["id"], max_citing)
                if got:
                    record(entry, today, *got)
                    continue
            s2 = s2_lookup(http, kind, value, max_citing)
            if s2:
                entry["resolved"] = {k: v for k, v in s2[0].items() if k != "count"}
                record(entry, today, s2[0]["count"], s2[1])
                continue
            entry["error"] = "Could not find this paper on OpenAlex" + (
                "" if os.environ.get("S2_API_KEY") else " (set S2_API_KEY to also try Semantic Scholar)")
        except Exception as e:  # keep other papers going
            log.exception("citation update failed for %s", q)
            entry["error"] = str(e)
    citations["updated"] = today
    return citations
