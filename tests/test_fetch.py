from arxiv_radar.fetch import RateLimiter, load_offline, normalize_id, parse_api, parse_rss


def test_normalize_id():
    assert normalize_id("oai:arXiv.org:2511.01234v2") == "2511.01234"
    assert normalize_id("http://arxiv.org/abs/2511.01234v1") == "2511.01234"
    assert normalize_id("arXiv:2511.01234") == "2511.01234"
    assert normalize_id("https://arxiv.org/pdf/hep-th/9901001v3") == "hep-th/9901001"


def test_parse_rss_drops_replacements(sample_day):
    papers, day = parse_rss((sample_day / "rss/cs.LG.xml").read_text())
    ids = {p["id"] for p in papers}
    assert "2511.00109" not in ids  # replace
    assert "2511.00106" in ids      # cross-list kept
    assert day.isoformat() == "2025-11-05"
    p = next(p for p in papers if p["id"] == "2511.00101")
    assert p["authors"] == ["Maria García", "Adrian Del Maestro", "Wei Zhang"]
    assert p["abstract"].startswith("We study offline reinforcement learning")
    assert p["categories"] == ["cs.LG", "cs.AI"]


def test_parse_api(sample_day):
    papers = parse_api((sample_day / "api/batch1.xml").read_text())
    p = next(p for p in papers if p["id"] == "2511.00104")
    assert p["doi"] == "10.1000/test.2025.104"
    assert p["primary_category"] == "cs.CL"
    assert p["pdf_url"].startswith("https://arxiv.org/pdf/2511.00104")
    assert p["authors"][1] == "Jörg Müller"


def test_load_offline_merges_and_dedupes(sample_day):
    papers, _ = load_offline(sample_day)
    ids = [p["id"] for p in papers]
    assert len(ids) == len(set(ids)) == 11
    p = next(p for p in papers if p["id"] == "2511.00106")
    assert p["announce_type"] == "cross"
    assert p["comment"] == "12 pages, 4 figures"  # from the API overlay


def test_rate_limiter_spacing():
    t = [0.0]
    slept = []
    rl = RateLimiter(3.0, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    rl.wait()
    t[0] += 1.0
    rl.wait()
    t[0] += 5.0
    rl.wait()
    assert slept == [2.0]
