import httpx

from arxiv_radar.citations import Http, classify_query, record, update_citations

WORK = {"id": "https://openalex.org/W100", "display_name": "My Paper", "publication_year": 2024,
        "doi": "https://doi.org/10.1/abc", "cited_by_count": 3}
CITING = {
    "meta": {"next_cursor": None},
    "results": [
        {"id": "https://openalex.org/W201", "display_name": "Citer one", "publication_year": 2025,
         "publication_date": "2025-10-01", "doi": None,
         "authorships": [{"author": {"display_name": "A B"}}]},
        {"id": "https://openalex.org/W202", "display_name": "Citer two", "publication_year": 2025,
         "publication_date": "2025-09-01", "doi": "https://doi.org/10.2/x", "authorships": []},
    ],
}


def mock_http(missing=()):
    def handler(req: httpx.Request):
        path = req.url.path
        if path in missing:
            return httpx.Response(404)
        if path.startswith("/works/doi:") or path == "/works/W100":
            return httpx.Response(200, json=WORK)
        if path == "/works" and "cites:W100" in req.url.params.get("filter", ""):
            return httpx.Response(200, json=CITING)
        if path == "/works" and req.url.params.get("search"):
            return httpx.Response(200, json={"results": [WORK]})
        return httpx.Response(404)

    return Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)


def test_classify_query():
    assert classify_query("2504.08385") == ("arxiv", "2504.08385")
    assert classify_query("arXiv:2504.08385v2") == ("arxiv", "2504.08385")
    assert classify_query("https://arxiv.org/abs/2504.08385") == ("arxiv", "2504.08385")
    assert classify_query("https://doi.org/10.1145/3292500.3330701") == ("doi", "10.1145/3292500.3330701")
    assert classify_query("Attention is all you need")[0] == "title"


def test_update_citations_resolves_and_records():
    my = {"papers": [{"query": "2504.08385", "label": "mine"}, {"query": "Some title"}]}
    out = update_citations(my, {"papers": {}}, "2025-11-05", http=mock_http())
    e = out["papers"]["2504.08385"]
    assert e["resolved"]["id"] == "W100" and e["count"] == 3 and e["label"] == "mine"
    assert [c["id"] for c in e["citing"]] == ["W201", "W202"]
    assert e["history"] == [{"date": "2025-11-05", "count": 3}]
    assert out["papers"]["Some title"]["resolved"]["title"] == "My Paper"


def test_removed_papers_are_dropped_and_errors_recorded(monkeypatch):
    monkeypatch.delenv("S2_API_KEY", raising=False)
    cites = {"papers": {"old": {"query": "old"}}}
    my = {"papers": [{"query": "10.9999/none"}]}
    out = update_citations(my, cites, "2025-11-05", http=mock_http(missing={"/works/doi:10.9999/none"}))
    assert "old" not in out["papers"]
    assert "Could not find" in out["papers"]["10.9999/none"]["error"]


def test_record_history_and_first_seen():
    e = {}
    record(e, "2025-11-01", 1, [{"id": "a"}])
    record(e, "2025-11-01", 2, [{"id": "a"}, {"id": "b"}])  # same day: overwrite
    record(e, "2025-11-02", 2, [{"id": "a"}, {"id": "b"}])
    assert e["history"] == [{"date": "2025-11-01", "count": 2}, {"date": "2025-11-02", "count": 2}]
    assert {c["id"]: c["first_seen"] for c in e["citing"]} == {"a": "2025-11-01", "b": "2025-11-01"}
