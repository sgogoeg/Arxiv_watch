"""BibTeX entries for saved papers (Library export). Mirrors site/app.js toBibtex()."""
from __future__ import annotations

import re
import unicodedata


def _ascii(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def cite_key(p: dict) -> str:
    authors = p.get("authors") or ["anon"]
    last = re.sub(r"[^a-z]", "", _ascii(authors[0].split(",")[0].split()[-1]).lower()) or "anon"
    year = _year(p)
    word = next((re.sub(r"[^a-z]", "", w.lower()) for w in _ascii(p.get("title", "")).split()
                 if len(re.sub(r"[^a-z]", "", w.lower())) > 3), "paper")
    return f"{last}{year}{word}"


def _year(p: dict) -> str:
    for k in ("published", "date"):
        if p.get(k):
            return str(p[k])[:4]
    m = re.match(r"(\d{2})(\d{2})\.", p.get("id", ""))
    return f"20{m.group(1)}" if m else ""


def _esc(s: str) -> str:
    return re.sub(r"([&%#_$])", r"\\\1", re.sub(r"\s+", " ", s or "").strip())


def to_bibtex(p: dict) -> str:
    fields = {
        "title": "{" + _esc(p.get("title", "")) + "}",
        "author": " and ".join(_esc(a) for a in p.get("authors", [])),
        "year": _year(p),
        "eprint": p.get("id", ""),
        "archivePrefix": "arXiv",
        "primaryClass": p.get("primary_category") or "",
        "url": p.get("abs_url") or f"https://arxiv.org/abs/{p.get('id', '')}",
    }
    if p.get("doi"):
        fields["doi"] = p["doi"]
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
    return f"@misc{{{cite_key(p)},\n{body}\n}}"


def folder_bibtex(library: dict, folder: str) -> str:
    papers = library.get("papers", {})
    ids = library.get("folders", {}).get(folder, {}).get("papers", [])
    return "\n\n".join(to_bibtex(papers[i]) for i in ids if i in papers) + "\n"
