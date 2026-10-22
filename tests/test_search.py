"""Search, filters, pagination and sorting: identical behaviour on every backend."""
import pytest

from conftest import REC, advance
from database import db


def add(repo, **kw):
    # summary follows the description so fixtures do not leak another record's words into searchable fields
    kw.setdefault("summary", kw.get("description", REC["summary"]))
    return db.insert_complaint({**REC, **kw}, db_path=repo)


@pytest.fixture
def populated(repo):
    ids = {
        "pothole": add(repo),
        "garbage": add(repo, description="Garbage overflowing outside Greenview Apartments", category="garbage",
                       location="Lake Road", priority="Low", department="Sanitation"),
        "light": add(repo, description="Streetlight flickering, the lane is dark at night", category="broken streetlight",
                     location="Temple Street", priority="Critical", department="Electrical"),
        "water": add(repo, description="Water pipe burst, 100% of the street is wet_ and flooded", category="water leakage",
                     location="Canal Road", priority="Medium", department="Water Board"),
    }
    advance(repo, ids["garbage"], "Resolved")
    return ids


def found(repo, **kw):
    return {r["complaint_id"] for r in db.search_complaints(db_path=repo, **kw)["rows"]}


def test_search_by_id_description_location(repo, populated):
    assert found(repo, search=populated["garbage"]) == {populated["garbage"]}
    assert found(repo, search="greenview") == {populated["garbage"]}
    assert found(repo, search="temple street") == {populated["light"]}


def test_partial_and_case_insensitive(repo, populated):
    assert found(repo, search="POTHO") == {populated["pothole"]}
    assert found(repo, search="flicker") == {populated["light"]}


def test_multiword_terms_match_in_any_order_and_any_field(repo, populated):
    assert found(repo, search="pothole market") == {populated["pothole"]}       # was 0 results before the fix
    assert found(repo, search="market pothole") == {populated["pothole"]}
    assert found(repo, search="pothole canal") == set()                          # every word must match


def test_wildcards_are_literal(repo, populated):
    assert found(repo, search="%") == {populated["water"]}
    assert found(repo, search="_") == {populated["water"]}
    assert found(repo, search=".*") == set()                                     # not a regex


def test_combined_filters(repo, populated):
    assert found(repo, category="garbage", status="Resolved") == {populated["garbage"]}
    assert found(repo, category="garbage", status="Submitted") == set()
    assert found(repo, priority=["High", "Critical"], search="road") == {populated["pothole"]}
    assert found(repo, status=["Submitted"], priority="Critical", category="broken streetlight") == {populated["light"]}


def test_empty_query_and_filters_return_everything_and_no_match_is_empty(repo, populated):
    assert len(found(repo)) == 4 and len(found(repo, search="   ")) == 4
    res = db.search_complaints(search="zzzz-no-such-thing", db_path=repo)
    assert res["rows"] == [] and res["total"] == 0 and res["pages"] == 1


def test_open_only_excludes_resolved(repo, populated):
    assert populated["garbage"] not in found(repo, open_only=True) and len(found(repo, open_only=True)) == 3


def test_invalid_filters_raise_clear_errors(repo, populated):
    with pytest.raises(ValueError):
        db.search_complaints(status="Teleported", db_path=repo)
    with pytest.raises(ValueError):
        db.search_complaints(category="noise", db_path=repo)
    with pytest.raises(ValueError):
        db.search_complaints(sort="random", db_path=repo)
    with pytest.raises(ValueError):
        db.search_complaints(created_from="2026-02-01", created_to="2026-01-01", db_path=repo)
    with pytest.raises(ValueError):
        db.search_complaints(created_from="not-a-date", db_path=repo)


def test_pagination_and_stable_ordering(repo):
    ids = [add(repo, location=f"Street {i}") for i in range(7)]
    p1 = db.search_complaints(page=1, page_size=3, db_path=repo)
    p3 = db.search_complaints(page=3, page_size=3, db_path=repo)
    assert (p1["total"], p1["pages"], len(p1["rows"]), len(p3["rows"])) == (7, 3, 3, 1)
    seen = [r["complaint_id"] for n in (1, 2, 3) for r in db.search_complaints(page=n, page_size=3, db_path=repo)["rows"]]
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 7                      # no gaps, no repeats
    assert db.search_complaints(page=99, page_size=3, db_path=repo)["page"] == 3     # clamped, not empty/crashing


def test_sort_by_priority(repo, populated):
    order = [r["priority"] for r in db.search_complaints(sort="priority", db_path=repo)["rows"]]
    assert order == ["Critical", "High", "Medium", "Low"]


def test_date_range_filter(repo, populated):
    today = db.get_complaint(populated["pothole"], repo)["created_at"][:10]
    assert len(found(repo, created_from=today, created_to=today)) == 4
    assert found(repo, created_to="2000-01-01") == set()
    assert found(repo, created_from="2999-01-01") == set()


def test_changes_are_visible_immediately(repo, populated):
    assert populated["pothole"] in found(repo, status="Submitted")
    db.update_status(populated["pothole"], "Under Review", db_path=repo)
    assert populated["pothole"] not in found(repo, status="Submitted")
    assert populated["pothole"] in found(repo, status="Under Review")


def test_count_by_matches_search_totals(repo, populated):
    for field, key in (("category", "category"), ("priority", "priority"), ("status", "status")):
        counts = db.count_by(field, db_path=repo)
        assert sum(counts.values()) == 4
        for value, n in counts.items():
            assert db.search_complaints(db_path=repo, **{key: value})["total"] == n


def test_sql_and_regex_injection_text_is_inert(repo, populated):
    assert found(repo, search="'; DROP TABLE complaints;--") == set()
    assert db.get_stats(db_path=repo)["total"] == 4


def test_backend_failure_is_an_error_not_an_empty_result(tmp_path):
    with pytest.raises(db.DatabaseError):
        db.search_complaints(db_path=str(tmp_path))          # a directory cannot be opened as a database
