import random

import numpy as np

from arxiv_radar import prefilter
from arxiv_radar.classify import build_training_set, classify, positives_per_topic, training_hash
from arxiv_radar.config import Topic
from arxiv_radar.embed import EmbeddingCache, HashingEmbedder, paper_text


def test_keyword_hits_word_boundaries():
    assert prefilter.keyword_hits("We use RL and LLMs", ["RL", "LLM"]) == ["RL"]
    assert prefilter.keyword_hits("LLM-as-a-judge evaluation", ["LLM-as-a-judge"]) == ["LLM-as-a-judge"]


def test_prefilter_ranks_relevant_first():
    emb = HashingEmbedder()
    topics = [Topic(name="RL", keywords=["reinforcement learning"], negative_keywords=["federated"])]
    ref = prefilter.TopicRefs("RL", ["s"], emb.encode(["offline reinforcement learning policy value"]))
    papers = [
        {"title": "Graph spectra", "abstract": "spectral bounds on graphs"},
        {"title": "Offline RL", "abstract": "offline reinforcement learning policy value ensembles"},
        {"title": "Federated", "abstract": "federated reinforcement learning privacy"},
    ]
    res = prefilter.score(papers, emb.encode([paper_text(p) for p in papers]), topics, [ref])
    assert res.keywords[1] == {"RL": ["reinforcement learning"]}
    order = prefilter.shortlist(res, 2)
    assert order[0] == 1
    assert prefilter.shortlist(res, 1, always={0}) == [1, 0]
    assert res.sims[2, 0] < res.raw[2, 0]  # negative keyword penalty


def test_percentile_scores():
    s = np.array([[0.1, 0.9], [0.5, 0.1], [0.3, 0.5]])
    p = prefilter.percentile_scores(s)
    assert p[:, 0].tolist() == [0.0, 1.0, 0.5]
    assert p[:, 1].tolist() == [1.0, 0.0, 0.5]


def test_training_set_labels():
    topics = [Topic(name="A", seed_ids=["s1"]), Topic(name="B")]
    seeds = {"s1": {"id": "s1", "title": "seed", "abstract": "x"}}
    saved = [
        ({"id": "p1", "title": "t1", "abstract": "a", "topics": ["B"]}, None, "inbox"),
        ({"id": "p2", "title": "t2", "abstract": "a", "topics": ["B"]}, "A", "folder-a"),
        ({"id": "p3", "title": "t3", "abstract": "a", "topics": []}, None, "misc"),  # no label -> skipped
    ]
    deleted = {"d1": {"title": "bad", "abstract": "z"}}
    bg = [{"id": f"b{i}", "title": f"bg {i}", "abstract": "q"} for i in range(10)]
    ex = build_training_set(topics, seeds, saved, deleted, bg, 3, random.Random(0))
    by = {e.key: e.labels for e in ex}
    assert by["s1"] == {"A"} and by["p1"] == {"B"} and by["p2"] == {"A"}
    assert "p3" not in by and by["d1"] == set()
    assert sum(k.startswith("b") for k in by) == 3
    assert positives_per_topic(ex, ["A", "B"]) == {"A": 2, "B": 1}
    assert training_hash(ex, ["A"], "m") == training_hash(list(reversed(ex)), ["A"], "m")


def test_classify_falls_back_without_labels(cfg):
    fb = np.array([[0.2, 0.8]], dtype=np.float32)
    scores, sources = classify(cfg, cfg.topics, [], ["text"], fb, "/nonexistent")
    assert sources == ["similarity", "similarity"]
    assert np.allclose(scores, fb)


def test_embedding_cache_roundtrip(tmp_path):
    emb = HashingEmbedder()
    c = EmbeddingCache(tmp_path / "e.npz", "hashing")
    v = c.embed(emb, [("a", "hello world"), ("b", "other text")])
    c.save()
    c2 = EmbeddingCache(tmp_path / "e.npz", "hashing")
    assert np.allclose(c2.vecs["a"], v[0])
    assert EmbeddingCache(tmp_path / "e.npz", "other-model").vecs == {}
