import json

from arxiv_radar import pipeline
from arxiv_radar.bibtex import cite_key, folder_bibtex, to_bibtex
from arxiv_radar.store import Store


def test_offline_run_end_to_end(cfg, sample_day):
    doc = pipeline.run(cfg, offline=str(sample_day))
    store = Store(cfg.data_dir)
    assert doc["date"] == "2025-11-05"
    assert store.read_day("2025-11-05")["counts"]["fetched"] == 11
    papers = doc["papers"]
    ids = [p["id"] for p in papers]
    # top_n=6 plus the followed-author paper (Jorg Muller) even if it ranks lower
    assert len(papers) >= 6 and "2511.00110" in ids and "2511.00104" in ids
    by = {p["id"]: p for p in papers}
    assert by["2511.00110"]["followed_authors"] == ["Jörg Müller"]
    assert by["2511.00101"]["best_topic"] == "Reinforcement learning"
    assert by["2511.00105"]["best_topic"] == "LLM evaluation"
    assert by["2511.00101"]["why"]["closest"]["id"] in {"2405.00001", "2405.00002"}
    assert all("tldr" in p for p in papers[:5])
    assert doc["score_source"] == {"Reinforcement learning": "similarity", "LLM evaluation": "similarity"}
    assert len(doc["background"]) == 11 - len(papers)
    idx = store.read("index.json")
    assert idx["days"][0]["date"] == "2025-11-05" and idx["topics"] == cfg.topic_names
    matches = store.read("author_matches.json")
    assert [m["id"] for m in matches["authors"]["Jörg Müller"]] == ["2511.00104", "2511.00110"] or \
        {m["id"] for m in matches["authors"]["Jörg Müller"]} == {"2511.00104", "2511.00110"}
    assert set(store.read("seeds.json")) == {"2405.00001", "2405.00002", "2405.00003", "2405.00004"}


def test_deleted_papers_are_hidden_and_saved_are_marked(cfg, sample_day):
    store = Store(cfg.data_dir)
    store.write("feedback.json", {"deleted": {"2511.00104": {"title": "x", "abstract": "y"}}})
    store.write("library.json", {"folders": {"To read": {"topic": None, "papers": ["2511.00105"]}},
                                 "papers": {"2511.00105": {"id": "2511.00105", "title": "LLM judge",
                                                            "abstract": "judge", "topics": ["LLM evaluation"]}}})
    doc = pipeline.run(cfg, offline=str(sample_day))
    ids = {p["id"]: p for p in doc["papers"]}
    assert "2511.00104" not in ids
    assert ids["2511.00105"]["saved_in"] == ["To read"]


def test_dry_run_writes_elsewhere(cfg, sample_day, tmp_path):
    out = tmp_path / "out"
    pipeline.run(cfg, offline=str(sample_day), out_dir=str(out))
    assert (out / "days/2025-11-05.json").exists()
    assert not (tmp_path / "data/days").exists()


def test_build_site(cfg, sample_day, tmp_path, monkeypatch):
    pipeline.run(cfg, offline=str(sample_day))
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/radar")
    out = pipeline.build_site(cfg, site_src="site", out=str(tmp_path / "_site"))
    conf = json.loads((out / "config.json").read_text())
    assert conf["repo"] == "me/radar" and conf["topics"] == cfg.topic_names
    assert (out / "index.html").exists() and (out / "data/days/2025-11-05.json").exists()
    html = (out / "index.html").read_text()
    assert 'src="app.js?v=' in html and 'href="style.css?v=' in html


def test_bibtex():
    p = {"id": "2511.00104", "title": "A Benchmark for Evaluating Long-Context Reasoning",
         "authors": ["Sara Okafor", "Jörg Müller"], "primary_category": "cs.CL", "published": "2025-11-03"}
    assert cite_key(p) == "okafor2025benchmark"
    b = to_bibtex(p)
    assert "author = {Sara Okafor and Jörg Müller}" in b and "eprint = {2511.00104}" in b
    lib = {"folders": {"f": {"papers": ["2511.00104", "missing"]}}, "papers": {"2511.00104": p}}
    assert folder_bibtex(lib, "f").count("@misc") == 1


def test_empty_day_writes_nothing(cfg, tmp_path):
    empty = tmp_path / "empty_day"
    (empty / "rss").mkdir(parents=True)
    (empty / "rss/cs.LG.xml").write_text(
        "<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>"
        "<pubDate>Sat, 08 Nov 2025 00:00:00 -0500</pubDate></channel></rss>")
    doc = pipeline.run(cfg, offline=str(empty))
    assert doc == {"date": "2025-11-08", "papers": []}
    assert not Store(cfg.data_dir).day_files()


def test_example_config_requires_categories(tmp_path):
    import pytest
    from arxiv_radar.config import load_config

    with pytest.raises(ValueError, match="categories"):
        load_config("config.example.yaml")


def test_seed_ids_drop_versions():
    from arxiv_radar.config import Topic

    assert Topic(name="t", seed_ids=["2305.02357v3", "arXiv:2403.09816"]).seed_ids == ["2305.02357", "2403.09816"]


def test_dashboard_categories_override_config(cfg, sample_day):
    store = Store(cfg.data_dir)
    assert store.categories(cfg.categories) == ["cs.LG", "cs.CL"]
    store.write("settings.json", {"categories": ["hep-ph", " gr-qc ", ""]})
    assert store.categories(cfg.categories) == ["hep-ph", "gr-qc"]
    store.write("settings.json", {"categories": ["cs.CL"]})
    doc = pipeline.run(cfg, offline=str(sample_day))
    assert doc["categories"] == ["cs.CL"]
    assert doc["counts"]["fetched"] == 5  # cs.CL primary or cross-listed papers only
    by = {p["id"]: p for p in doc["papers"]}
    assert by["2511.00106"]["announce_type"] == "new"  # primary cs.CL, even though it came via the cs.LG feed
    assert doc["awards"]["pool"] == 11  # awards still see the whole listing


def test_fetch_skips_failing_category(monkeypatch, sample_day):
    import pytest
    from arxiv_radar import fetch

    class Client:
        def get(self, url, params=None):
            if url.endswith("bad.cat"):
                raise RuntimeError("boom")
            if "rss" in url:
                return (sample_day / "rss/cs.LG.xml").read_text()
            return (sample_day / "api/batch1.xml").read_text()

    papers, _ = fetch.fetch_today(["cs.LG", "bad.cat"], Client())
    assert len(papers) == 7
    with pytest.raises(RuntimeError):
        fetch.fetch_today(["bad.cat"], Client())


def test_combined_score():
    assert pipeline.combined_score({"a": 0.8}) == 0.8
    assert pipeline.combined_score({"a": 0.5, "b": 0.5}) == 0.75
    assert pipeline.combined_score({"a": 0.9, "b": 0.0}) == 0.9


def test_dashboard_topics_override_config(cfg, sample_day):
    store = Store(cfg.data_dir)
    store.write("settings.json", {"topics": [
        {"name": "Robotics", "description": "Legged robots and locomotion", "seed_ids": [], "keywords": ["robot"]},
        {"name": "LLM evaluation", "description": "Benchmarks for language models", "seed_ids": ["2405.00003v2"]},
        {"name": ""}, {"description": "missing name"}, {"name": "Robotics"},
    ]})
    assert [t.name for t in store.topics(cfg.topics)] == ["Robotics", "LLM evaluation"]
    doc = pipeline.run(cfg, offline=str(sample_day))
    assert doc["topics"] == ["Robotics", "LLM evaluation"]
    by = {p["id"]: p for p in doc["papers"]}
    assert by["2511.00110"]["best_topic"] == "Robotics"
    combined = [p["combined"] for p in doc["papers"]]
    assert combined == sorted(combined, reverse=True)
    assert Store(cfg.data_dir).read("index.json")["topics"] == ["Robotics", "LLM evaluation"]
