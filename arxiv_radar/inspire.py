"""INSPIRE-HEP links for arXiv papers.

Paper links on the dashboard go to the INSPIRE record when one exists and to arXiv otherwise.
New papers usually show up in INSPIRE within a day or two, so recent days are re-checked
on later runs.
"""
from __future__ import annotations

import json
import logging

from .citations import Http
from .fetch import normalize_id

log = logging.getLogger(__name__)

API = "https://inspirehep.net/api/literature"
RECORD = "https://inspirehep.net/literature/{}"


def parse_hits(res: dict) -> dict[str, str]:
    """INSPIRE search response -> {arxiv_id: record url}."""
    out = {}
    for hit in (res or {}).get("hits", {}).get("hits", []):
        md = hit.get("metadata", {})
        recid = md.get("control_number") or hit.get("id")
        if not recid:
            continue
        for e in md.get("arxiv_eprints", []):
            if e.get("value"):
                out[normalize_id(e["value"])] = RECORD.format(recid)
    return out


def lookup(ids: list[str], http: Http | None = None, batch: int = 40) -> dict[str, str]:
    """Find INSPIRE records for arXiv IDs, `batch` IDs per request."""
    http = http or Http(min_interval=0.4)  # INSPIRE allows 15 requests per 5 s
    ids = list(dict.fromkeys(i for i in ids if i))
    found: dict[str, str] = {}
    for k in range(0, len(ids), batch):
        chunk = ids[k : k + batch]
        q = " or ".join(f"arxiv:{i}" for i in chunk)
        res = http.get_json(API, {"q": q, "size": len(chunk), "fields": "control_number,arxiv_eprints"})
        if res is None:
            log.warning("INSPIRE lookup failed for %d papers; keeping arXiv links", len(chunk))
            continue
        found.update({i: u for i, u in parse_hits(res).items() if i in chunk})
    return found


def add_links(papers: list[dict], http: Http | None = None) -> int:
    """Set paper['inspire_url'] in place for papers that lack it. Returns how many were added."""
    todo = [p for p in papers if not p.get("inspire_url")]
    if not todo:
        return 0
    found = lookup([p["id"] for p in todo], http)
    for p in todo:
        if p["id"] in found:
            p["inspire_url"] = found[p["id"]]
    return sum(1 for p in todo if p.get("inspire_url"))


def recheck_recent(store, writer, days: int, http: Http | None = None, skip: str | None = None) -> None:
    """Fill INSPIRE links that were missing on earlier days, and in author matches."""
    http = http or Http(min_interval=0.4)
    for f in store.day_files()[-days:]:
        if f.stem == skip:
            continue
        doc = json.loads(f.read_text())
        if add_links(doc.get("papers", []), http):
            writer.write_day(f.stem, doc)
    matches = store.read("author_matches.json")
    papers = [p for lst in matches.get("authors", {}).values() for p in lst]
    if add_links(papers, http):
        writer.write("author_matches.json", matches)
