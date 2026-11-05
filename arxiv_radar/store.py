"""JSON files under data/ shared between the pipeline and the dashboard.

Files the dashboard edits (through GitHub commits):
  feedback.json   {"deleted": {arxiv_id: {"date", "title", "abstract", "topics"}},
                   "cared":   {arxiv_id: {...same fields}},   ("I care": train on it, no folder)
                   "wacky_votes": {date: {"winner": arxiv_id | None, "candidates": [ids]}}}
  library.json    {"folders": {name: {"topic": str|None, "papers": [arxiv_id]}},
                   "papers": {arxiv_id: paper metadata}}
  authors.json    {"authors": [{"name", "openalex_id", "added"}]}
  my_papers.json  {"papers": [{"query", "label", "added"}]}
  settings.json   {"categories": [...]}   (overrides config.yaml categories when set)

Files the pipeline writes:
  days/YYYY-MM-DD.json, index.json, author_matches.json, citations.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "feedback.json": {"deleted": {}, "cared": {}, "wacky_votes": {}},
    "library.json": {"folders": {}, "papers": {}},
    "authors.json": {"authors": []},
    "my_papers.json": {"papers": []},
    "settings.json": {},
    "author_matches.json": {"authors": {}, "backfilled": []},
    "citations.json": {"papers": {}, "updated": None},
    "index.json": {"days": [], "topics": [], "generated": None},
}


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path(self, name: str) -> Path:
        return self.root / name

    def read(self, name: str) -> Any:
        p = self.path(name)
        if not p.exists():
            return json.loads(json.dumps(DEFAULTS.get(name, {})))
        return json.loads(p.read_text())

    def write(self, name: str, data: Any) -> Path:
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
        return p

    def day_files(self) -> list[Path]:
        return sorted((self.root / "days").glob("*.json"))

    def read_day(self, date: str) -> dict | None:
        p = self.root / "days" / f"{date}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def write_day(self, date: str, data: dict) -> Path:
        return self.write(f"days/{date}.json", data)

    # Convenience views used for training ---------------------------------
    def saved_papers(self) -> list[tuple[dict, str | None, str]]:
        """(paper, folder topic or None, folder name) for every paper saved in a folder."""
        lib = self.read("library.json")
        papers = lib.get("papers", {})
        out = []
        for folder, info in lib.get("folders", {}).items():
            for pid in info.get("papers", []):
                if pid in papers:
                    out.append((papers[pid], info.get("topic"), folder))
        return out

    def cared_papers(self) -> dict[str, dict]:
        return self.read("feedback.json").get("cared", {})

    def positive_papers(self) -> list[tuple[dict, str | None, str | None]]:
        """Saved papers plus "I care" papers, as (paper, folder topic, folder or None)."""
        out = list(self.saved_papers())
        saved_ids = {p["id"] for p, _, _ in out}
        for pid, paper in self.cared_papers().items():
            if pid not in saved_ids:
                out.append(({**paper, "id": pid}, None, None))
        return out

    def deleted_papers(self) -> dict[str, dict]:
        return self.read("feedback.json").get("deleted", {})

    def categories(self, default: list[str]) -> list[str]:
        """Categories chosen on the dashboard, falling back to config.yaml."""
        cats = [c.strip() for c in self.read("settings.json").get("categories") or [] if c and c.strip()]
        return cats or list(default)

    def followed_author_names(self, extra: list[str] | None = None) -> list[str]:
        names = [a["name"] for a in self.read("authors.json").get("authors", []) if a.get("name")]
        for n in extra or []:
            if n not in names:
                names.append(n)
        return names
