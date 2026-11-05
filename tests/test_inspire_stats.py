import httpx

from arxiv_radar import inspire, pipeline, stats
from arxiv_radar.citations import Http
from arxiv_radar.store import Store

HITS = {"hits": {"hits": [
    {"id": "2700001", "metadata": {"control_number": 2700001, "arxiv_eprints": [{"value": "2611.05504"}]}},
    {"id": "2700002", "metadata": {"control_number": 2700002, "arxiv_eprints": [{"value": "2611.05566"}]}},
]}}


def mock(calls):
    def handler(req):
        calls.append(req.url.params["q"])
        return httpx.Response(200, json=HITS)
    return Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)


def test_lookup_batches_and_maps_ids():
    calls = []
    found = inspire.lookup(["2611.05504", "2611.05566", "2611.09999"], mock(calls), batch=2)
    assert found == {"2611.05504": "https://inspirehep.net/literature/2700001",
                     "2611.05566": "https://inspirehep.net/literature/2700002"}
    assert calls == ["arxiv:2611.05504 or arxiv:2611.05566", "arxiv:2611.09999"]


def test_add_links_only_missing():
    papers = [{"id": "2611.05504"}, {"id": "2611.05566", "inspire_url": "keep"}, {"id": "2611.09999"}]
    calls = []
    assert inspire.add_links(papers, mock(calls)) == 1
    assert papers[0]["inspire_url"].endswith("2700001") and papers[1]["inspire_url"] == "keep"
    assert "inspire_url" not in papers[2] and calls == ["arxiv:2611.05504 or arxiv:2611.09999"]


def test_lookup_failure_keeps_arxiv_links():
    def handler(req):
        return httpx.Response(404)
    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    assert inspire.lookup(["2611.05504"], http) == {}


def test_top_terms_ignores_stopwords_and_latex():
    titles = ["Gravitational waves from domain walls", "Domain walls and $\\Lambda$CDM",
              "Gravitational waves at PTAs", "Dark matter"]
    terms = dict(stats.top_terms(titles))
    assert terms["gravitational waves"] == 2 and terms["domain walls"] == 2
    assert "gravitational" not in terms  # covered by the phrase with the same count
    assert "and" not in terms and "lambda" not in terms


def test_compute_groups():
    allp = [{"id": str(i), "title": "x", "authors": ["A"] * (i + 1), "primary_category": "hep-ph" if i < 3 else "gr-qc",
             "announce_type": "new" if i % 2 else "cross"} for i in range(5)]
    entries = [{**allp[0], "topics": ["T"], "score": 0.9, "followed_authors": []},
               {**allp[3], "topics": [], "score": 0.2, "followed_authors": ["A"]},
               {**allp[1], "topics": [], "score": 0.1, "followed_authors": []}]
    s = stats.compute(allp, [0.9, 0.5, 0.4, 0.6, 0.1], entries, ["T"], 0.5)
    g, p = s["general"], s["personal"]
    assert g["count"] == 5 and g["by_category"][0] == ["hep-ph", 3]
    assert sum(b["passed"] for b in g["similarity_hist"]) == 2 and sum(b["other"] for b in g["similarity_hist"]) == 3
    assert p["count"] == 2 and p["by_topic"] == [["T", 1]] and p["followed"] == 1
    assert p["hit_rate_by_category"] == [["hep-ph", 1, 3]]  # gr-qc has < 3 papers
    assert sum(n for _, n in p["score_hist"]) == 3


def test_cared_papers_are_positives_and_day_has_stats(cfg, sample_day):
    store = Store(cfg.data_dir)
    store.write("feedback.json", {"deleted": {}, "cared": {
        "2511.00105": {"title": "LLM judge", "abstract": "judge bias", "topics": ["LLM evaluation"]}}})
    assert [p["id"] for p, _, f in store.positive_papers()] == ["2511.00105"]
    doc = pipeline.run(cfg, offline=str(sample_day))
    assert "saved_in" not in next(p for p in doc["papers"] if p["id"] == "2511.00105")
    assert doc["stats"]["general"]["count"] == 11
    assert doc["stats"]["personal"]["count"] >= 1
