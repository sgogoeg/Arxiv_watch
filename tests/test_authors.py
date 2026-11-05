from datetime import date

from arxiv_radar.authors import AuthorMatcher, backfill, name_key, same_person, update_matches


def test_name_key():
    assert name_key("Adrian Del Maestro").last == "del maestro"
    assert name_key("Müller, Jörg") == name_key("Jorg Muller")
    assert name_key("J. R. R. Tolkien").first == "j"
    assert name_key("Martin Luther King Jr.").last == "king"


def test_same_person_variants():
    assert same_person("A. Del Maestro", "Adrian Del Maestro")
    assert same_person("Jörg Müller", "Jorg Muller")
    assert same_person("Müller, J.", "Jörg Müller")
    assert same_person("Wei Zhang", "W. Zhang")
    assert not same_person("Wei Zhang", "Li Zhang")
    assert not same_person("Adrian Maestro", "Adrian Del Maestro")
    assert not same_person("Sam Smith", "Samantha Jones")
    assert same_person("Sam Smith", "Samuel Smith")  # prefix-compatible first names
    assert not same_person("Sara Okafor", "Simon Okafor")


def test_matcher_on_paper():
    m = AuthorMatcher(["Jörg Müller", "Sara Okafor"])
    assert m.match({"authors": ["Jorg Muller", "Yuki Tanaka"]}) == ["Jörg Müller"]
    assert m.match({"authors": ["Rui Silva", "S. Okafor"]}) == ["Sara Okafor"]
    assert m.match({"authors": ["Nobody"]}) == []


def test_update_matches_dedupes_and_sorts():
    m = {"authors": {}, "backfilled": []}
    p1 = {"id": "1", "title": "a", "followed_authors": ["X Y"], "date": "2025-11-01"}
    p2 = {"id": "2", "title": "b", "followed_authors": ["X Y"], "date": "2025-11-03"}
    update_matches(m, [p1, p2, p1], "2025-11-03")
    assert [x["id"] for x in m["authors"]["X Y"]] == ["2", "1"]


def test_backfill_filters_by_date_and_name(monkeypatch):
    found = [
        {"id": "a", "title": "t", "authors": ["Jorg Muller"], "published": "2025-11-01T00:00:00Z"},
        {"id": "b", "title": "t", "authors": ["Jorg Muller"], "published": "2025-01-01T00:00:00Z"},
        {"id": "c", "title": "t", "authors": ["Hans Muller"], "published": "2025-11-02T00:00:00Z"},
    ]
    monkeypatch.setattr("arxiv_radar.fetch.search_author", lambda client, name: found)
    m = backfill(None, ["Jörg Müller"], {"authors": {}, "backfilled": []}, date(2025, 11, 5), 30)
    assert [x["id"] for x in m["authors"]["Jörg Müller"]] == ["a"]
    assert m["backfilled"] == ["Jörg Müller"]
    # second call does not search again
    monkeypatch.setattr("arxiv_radar.fetch.search_author", lambda *a: (_ for _ in ()).throw(AssertionError))
    backfill(None, ["Jörg Müller"], m, date(2025, 11, 5), 30)
