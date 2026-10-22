import pytest

from database import db

REC = {"description": "Large pothole on Main Road", "category": "pothole", "location": "Main Road",
       "priority": "High", "department": "Public Works Department (Roads)", "summary": "sum"}


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "t.db")
    db.init_db(p)
    return p


def test_init_is_idempotent(path):
    db.init_db(path)
    assert db.get_stats(path)["total"] == 0


def test_insert_and_get(path):
    cid = db.insert_complaint(REC, db_path=path)
    row = db.get_complaint(cid, path)
    assert cid.startswith("CF-") and row["status"] == "Submitted" and row["created_at"]


def test_ids_are_unique(path):
    ids = {db.insert_complaint(REC, db_path=path) for _ in range(30)}
    assert len(ids) == 30


def test_validation_errors(path):
    with pytest.raises(ValueError):
        db.insert_complaint({**REC, "description": ""}, db_path=path)
    with pytest.raises(ValueError):
        db.insert_complaint({**REC, "category": "noise"}, db_path=path)
    with pytest.raises(ValueError):
        db.insert_complaint({**REC, "status": "Done"}, db_path=path)


def test_update_status_logs_history(path):
    cid = db.insert_complaint(REC, db_path=path)
    assert db.update_status(cid, "Under Review", "crew assigned", db_path=path)
    assert db.get_complaint(cid, path)["status"] == "Under Review"
    log = db.get_activity(cid, db_path=path)
    assert log[-1]["decision"] == "Submitted -> Under Review"


def test_update_status_invalid_and_missing(path):
    cid = db.insert_complaint(REC, db_path=path)
    with pytest.raises(ValueError):
        db.update_status(cid, "Teleported", db_path=path)
    assert db.update_status("CF-NOPE", "Resolved", db_path=path) is False


def test_filters_and_stats(path):
    a = db.insert_complaint(REC, db_path=path)
    db.insert_complaint({**REC, "category": "garbage", "priority": "Low", "location": "Park"}, db_path=path)
    for step in ("Under Review", "In Progress", "Resolved"):
        db.update_status(a, step, db_path=path)
    assert len(db.list_complaints(category="garbage", db_path=path)) == 1
    assert len(db.list_complaints(status="Resolved", db_path=path)) == 1
    assert len(db.list_complaints(search="Park", db_path=path)) == 1
    s = db.get_stats(path)
    assert (s["total"], s["resolved"], s["pending"]) == (2, 1, 1)


def test_sql_injection_text_is_inert(path):
    db.insert_complaint({**REC, "description": "x'); DROP TABLE complaints;-- pothole"}, db_path=path)
    assert db.list_complaints(search="'; DROP TABLE", db_path=path) == []
    assert db.get_stats(path)["total"] == 1


def test_delete_and_reset(path):
    cid = db.insert_complaint(REC, trace=[{"agent": "A", "step": "s"}], db_path=path)
    assert db.delete_complaint(cid, path) and db.get_complaint(cid, path) is None
    assert db.get_activity(cid, db_path=path) == []
    db.insert_complaint(REC, db_path=path)
    db.reset_db(path)
    assert db.get_stats(path)["total"] == 0


def test_seed(tmp_path):
    from database.seed import seed_database
    p = str(tmp_path / "seed.db")
    assert seed_database(db_path=p) == 14
    assert seed_database(db_path=p) == 0          # does not duplicate
    assert seed_database(reset=True, db_path=p) == 14
    assert db.get_stats(p)["resolved"] == 2
