"""Followed-author matching by normalized name.

Matching rule: last names must be equal, and first names must be compatible
(same initial, and if both are spelled out, one is a prefix of the other).
Middle names are ignored. This handles "A. Del Maestro" vs "Adrian Del Maestro",
"Müller, Jörg" vs "Jorg Muller", and similar variants.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la", "le", "dos", "das", "ter", "ten"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


@dataclass(frozen=True)
class NameKey:
    first: str  # may be a single initial
    last: str   # includes particles: "del maestro"

    @property
    def initial(self) -> str:
        return self.first[:1]


def _ascii(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def name_key(name: str) -> NameKey | None:
    s = _ascii(name).lower().strip()
    if not s:
        return None
    if "," in s:  # "Last, First"
        last, first = [p.strip() for p in s.split(",", 1)]
        s = f"{first} {last}"
    s = re.sub(r"[.\-']", " ", s)
    s = re.sub(r"[^a-z ]", "", s)
    tokens = [t for t in s.split() if t not in _SUFFIXES]
    if not tokens:
        return None
    if len(tokens) == 1:
        return NameKey(first="", last=tokens[0])
    # last name = final token plus any lowercase particles right before it
    i = len(tokens) - 1
    while i > 1 and tokens[i - 1] in _PARTICLES:
        i -= 1
    return NameKey(first=tokens[0], last=" ".join(tokens[i:]))


def same_person(a: str | NameKey, b: str | NameKey) -> bool:
    ka = a if isinstance(a, NameKey) else name_key(a)
    kb = b if isinstance(b, NameKey) else name_key(b)
    if not ka or not kb:
        return False
    # Compare last names ignoring particles so "Maestro" never matches but "del maestro" == "del maestro"
    if ka.last.replace(" ", "") != kb.last.replace(" ", ""):
        return False
    if not ka.first or not kb.first:
        return True
    if ka.initial != kb.initial:
        return False
    if len(ka.first) > 1 and len(kb.first) > 1:
        return ka.first.startswith(kb.first) or kb.first.startswith(ka.first)
    return True


class AuthorMatcher:
    def __init__(self, followed: list[str]):
        self.followed = [(n, name_key(n)) for n in followed if name_key(n)]
        # index by squashed last name for speed
        self._by_last: dict[str, list[tuple[str, NameKey]]] = {}
        for n, k in self.followed:
            self._by_last.setdefault(k.last.replace(" ", ""), []).append((n, k))

    def match(self, paper: dict) -> list[str]:
        hits: list[str] = []
        for author in paper.get("authors", []):
            k = name_key(author)
            if not k:
                continue
            for followed_name, fk in self._by_last.get(k.last.replace(" ", ""), []):
                if followed_name not in hits and same_person(k, fk):
                    hits.append(followed_name)
        return hits


def update_matches(matches: dict, papers: list[dict], day: str, keep: int = 200) -> dict:
    """Add papers (already tagged with 'followed_authors') to author_matches.json data."""
    by_author = matches.setdefault("authors", {})
    for p in papers:
        for name in p.get("followed_authors", []):
            lst = by_author.setdefault(name, [])
            if any(x["id"] == p["id"] for x in lst):
                continue
            lst.append(
                {
                    "id": p["id"],
                    "title": p["title"],
                    "authors": p.get("authors", []),
                    "date": p.get("date") or day,
                    "abs_url": p.get("abs_url"),
                    "pdf_url": p.get("pdf_url"),
                    "abstract": p.get("abstract", ""),
                    "categories": p.get("categories", []),
                }
            )
    for name, lst in by_author.items():
        lst.sort(key=lambda x: x.get("date") or "", reverse=True)
        del lst[keep:]
    return matches


def backfill(client, names: list[str], matches: dict, today: date, days: int = 30) -> dict:
    """Search arXiv for recent papers by authors that were never backfilled."""
    from .fetch import search_author

    done = set(matches.setdefault("backfilled", []))
    since = today - timedelta(days=days)
    for name in names:
        if name in done:
            continue
        try:
            found = search_author(client, name)
        except Exception as e:
            log.warning("backfill for %s failed: %s", name, e)
            continue
        matcher = AuthorMatcher([name])
        recent = []
        for p in found:
            pub = (p.get("published") or "")[:10]
            try:
                if datetime.fromisoformat(pub).date() < since:
                    continue
            except ValueError:
                continue
            if matcher.match(p):
                recent.append({**p, "followed_authors": [name], "date": pub})
        update_matches(matches, recent, today.isoformat())
        matches["backfilled"].append(name)
        log.info("backfilled %d recent papers for %s", len(recent), name)
    return matches
