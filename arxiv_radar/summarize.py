"""One-sentence TL;DRs for the top papers, using a small local model."""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

PROMPT = "Summarize the main contribution of this research paper in one sentence.\nTitle: {title}\nAbstract: {abstract}"


def first_sentence(text: str, max_chars: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"^(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m else text
    return s if len(s) <= max_chars else s[: max_chars - 1].rstrip() + "…"


class FlanT5Summarizer:
    def __init__(self, model: str):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model)
        self.model.eval()

    def __call__(self, paper: dict) -> str:
        import torch

        prompt = PROMPT.format(title=paper.get("title", ""), abstract=paper.get("abstract", ""))
        inputs = self.tok(prompt, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=60, num_beams=2)
        return self.tok.decode(out[0], skip_special_tokens=True).strip()


def summarize(papers: list[dict], cfg) -> None:
    """Set paper['tldr'] for the first cfg.top_k papers (in place)."""
    if cfg.backend == "none":
        return
    top = papers[: cfg.top_k]
    model = None
    if cfg.backend == "flan-t5":
        try:
            model = FlanT5Summarizer(cfg.model)
        except Exception as e:
            log.warning("summarizer unavailable (%s); using first sentences", e)
    start = time.monotonic()
    for p in top:
        text = None
        if model and time.monotonic() - start < cfg.time_budget_seconds:
            try:
                text = model(p)
            except Exception as e:
                log.warning("summary failed for %s: %s", p.get("id"), e)
        p["tldr"] = text or first_sentence(p.get("abstract", ""))
        p["tldr_source"] = "model" if text else "abstract"
