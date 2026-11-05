import random

from arxiv_radar import awards
from arxiv_radar.fetch import filter_by_categories, in_categories
from arxiv_radar.store import Store


def P(i, **kw):
    return {"id": f"2611.{i:05d}", "title": kw.pop("title", f"Paper {i}"), "authors": kw.pop("authors", ["A B"]),
            "abstract": kw.pop("abstract", "We study things."), **kw}


def test_in_categories_and_filter():
    assert in_categories("astro-ph.CO", ["astro-ph"]) and in_categories("hep-ph", ["hep-ph"])
    assert not in_categories("hep-phx", ["hep-ph"]) and not in_categories("astro-ph", ["astro-ph.CO"])
    ps = [P(1, categories=["hep-th", "hep-ph"], primary_category="hep-th"), P(2, categories=["cs.LG"], primary_category="cs.LG")]
    out = filter_by_categories(ps, ["hep-ph"])
    assert [p["id"] for p in out] == ["2611.00001"] and out[0]["announce_type"] == "cross"


def test_page_count():
    assert awards.page_count("45 pages, 12 figures") == 45
    assert awards.page_count("30+12 pages; v2") == 42
    assert awards.page_count("Ph.D. thesis, 251 pp.") == 251
    assert awards.page_count("accepted in PRD") is None and awards.page_count(None) is None


def test_longest():
    r = awards.longest([P(1, comment="10 pages"), P(2, comment="108 pages, 30 figures"), P(3), P(4, comment="50 pages")])
    assert r["winner"]["pages"] == 108 and [x["pages"] for x in r["runners_up"]] == [50, 10]
    assert r["with_page_count"] == 3


def test_most_submissions_merges_names_and_skips_collaborations():
    ps = [P(1, authors=["Florian Beutler", "X Y"]), P(2, authors=["F. Beutler"]), P(3, authors=["Beutler, F."]),
          P(4, authors=["Ana Lima", "B C"]), P(5, authors=["A. Lima"]),
          P(6, authors=["ATLAS Collaboration", "Ana Lima"]), P(7, authors=[f"Person {i}" for i in range(40)] + ["Ana Lima"])]
    r = awards.most_submissions(ps)
    assert r["count"] == 3 and r["tied"] == 1
    assert r["winners"][0]["name"] == "Florian Beutler" and len(r["winners"][0]["papers"]) == 3
    assert r["excluded_collaborations"] == 2


def test_drama_detection():
    ps = [P(1, title="Comment on \"Dark matter from nothing\" [arXiv:2610.01234]"),
          P(2, title="Reply to the comment by Smith et al."),
          P(3, title="A new model", comment="Comment on arXiv:2609.00001; 4 pages"),
          P(4, title="Dark matter", abstract="In this comment we show that the recent claim is incorrect."),
          P(5, title="Erratum: Comment on something"),
          P(6, title="Inflation", abstract="We show the result is incorrect in a regime.")]
    hits = awards.drama(ps, n=10)
    ids = [h["id"] for h in hits]
    assert ids[0] == "2611.00001" and hits[0]["target"]["id"] == "2610.01234"
    assert set(ids) == {"2611.00001", "2611.00002", "2611.00003", "2611.00004"}


def test_wacky_candidates_and_training():
    ps = [P(i, title=t) for i, t in enumerate([
        "Spectral bounds for operators", "Spectral analysis of operators", "Do androids dream of electric sheep?",
        "Operators and spectral bounds revisited", "Bounds on spectral operators", "Spectral operators II"])]
    corpus = [p["title"] for p in ps] * 5
    r = awards.wacky(ps, [0.9] * len(ps), corpus, awards.PRIOR, random.Random(0))
    cands = r["candidates"]
    assert len(cands) == 3 and sum(c["explore"] for c in cands) == 1
    assert "2611.00002" in {c["id"] for c in cands}  # the question title is a top pick
    assert set(cands[0]["features"]) == set(awards.FEATURES)
    # Votes that always pick the title with alliteration push that weight up
    a, b = {f: 0.0 for f in awards.FEATURES}, {f: 0.0 for f in awards.FEATURES}
    a["alliteration"] = 1.5
    w = awards.train_weights([(a, b)] * 20)
    assert w["alliteration"] > awards.PRIOR["alliteration"]
    assert abs(w["rarity"] - awards.PRIOR["rarity"]) < 1e-6


def test_retrain_from_votes(tmp_path):
    store = Store(tmp_path)
    feats = {f: 0.0 for f in awards.FEATURES}
    win = {**feats, "question": 2.0}
    store.write_day("2026-11-05", {"awards": {"wacky": {"candidates": [
        {"id": "a", "features": win}, {"id": "b", "features": feats}, {"id": "c", "features": feats}]}}})
    store.write("feedback.json", {"wacky_votes": {"2026-11-05": {"winner": "a"}, "2026-11-04": {"winner": "z"}}})
    w = awards.retrain(store, store)
    assert w["question"] > awards.PRIOR["question"]
    model = store.read("awards_model.json")
    assert model["votes"] == 2 and model["pairs"] == 2
    assert awards.load_weights(store)["question"] == w["question"]
