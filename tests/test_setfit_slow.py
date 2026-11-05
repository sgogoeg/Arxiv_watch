"""End-to-end SetFit training with a tiny randomly initialized BERT built locally (no downloads)."""
import re

import pytest

pytest.importorskip("setfit")
pytestmark = pytest.mark.slow


@pytest.fixture
def tiny_model(tmp_path, sample_day):
    from transformers import BertConfig, BertModel, BertTokenizer

    words = set()
    for f in sample_day.rglob("*.xml"):
        words |= set(re.findall(r"[a-z]+", f.read_text().lower()))
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + sorted(words)
    d = tmp_path / "tiny-bert"
    d.mkdir()
    (d / "vocab.txt").write_text("\n".join(vocab))
    BertTokenizer(str(d / "vocab.txt")).save_pretrained(d)
    cfg = BertConfig(vocab_size=len(vocab), hidden_size=32, num_hidden_layers=1, num_attention_heads=2,
                     intermediate_size=64, max_position_embeddings=512)
    BertModel(cfg).save_pretrained(d)
    return str(d)


def test_setfit_trains_and_reuses_model(cfg, sample_day, tiny_model):
    from arxiv_radar import pipeline
    from arxiv_radar.store import Store

    cfg = cfg.model_copy(update={
        "embedding": cfg.embedding.model_copy(update={"backend": "sentence-transformers", "model": tiny_model}),
        "classifier": cfg.classifier.model_copy(update={"min_positives_per_topic": 2, "max_steps": 20, "num_iterations": 2}),
    })
    store = Store(cfg.data_dir)
    store.write("feedback.json", {"deleted": {"x1": {"title": "Galaxy rotation curves", "abstract": "astronomy dark matter"}}})

    doc = pipeline.run(cfg, offline=str(sample_day), day="2025-11-05")
    assert set(doc["score_source"].values()) == {"model"}
    assert all(0.0 <= s <= 1.0 for p in doc["papers"] for s in p["scores"].values())
    meta = store.root.parent / "cache" / "model" / "radar_meta.json"
    assert meta.exists()
    mtime = meta.stat().st_mtime

    # Same training data: the cached model is reused, not retrained.
    pipeline.run(cfg, offline=str(sample_day), day="2025-11-05")
    assert meta.stat().st_mtime == mtime


def test_setfit_single_topic(cfg, sample_day, tiny_model):
    from arxiv_radar import pipeline

    cfg = cfg.model_copy(update={
        "topics": cfg.topics[:1],
        "embedding": cfg.embedding.model_copy(update={"backend": "sentence-transformers", "model": tiny_model}),
        "classifier": cfg.classifier.model_copy(update={"min_positives_per_topic": 2, "max_steps": 10, "num_iterations": 2}),
    })
    from arxiv_radar.store import Store
    Store(cfg.data_dir).write("feedback.json", {"deleted": {"x1": {"title": "Galaxies", "abstract": "astronomy"}}})
    doc = pipeline.run(cfg, offline=str(sample_day))
    assert doc["score_source"] == {"Reinforcement learning": "model"}
    assert all(0.0 <= p["scores"]["Reinforcement learning"] <= 1.0 for p in doc["papers"])
