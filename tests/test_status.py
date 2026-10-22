"""Status transitions, conflicts and history; legacy database upgrade."""
import sqlite3

import pytest

import config
from conftest import REC, advance
from database import db
from database.sqlite_backend import SQLiteBackend


def test_only_one_step_forward_is_allowed(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    for bad in ("In Progress", "Resolved", "Submitted"):
        with pytest.raises(db.InvalidTransition):
            db.update_status(cid, bad, db_path=repo)
    assert db.get_complaint(cid, repo)["status"] == "Submitted"              # a refused change changes nothing
    assert len(db.get_status_history(cid, repo)) == 1
    advance(repo, cid, "Resolved")
    assert [h["to_status"] for h in db.get_status_history(cid, repo)] == config.STATUSES
    with pytest.raises(db.InvalidTransition):                                # Resolved is final (no silent reopen)
        db.update_status(cid, "Submitted", db_path=repo)


def test_invalid_status_and_missing_complaint(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    with pytest.raises(ValueError):
        db.update_status(cid, "Teleported", db_path=repo)
    assert db.update_status("CF-20000101-0000", "Under Review", db_path=repo) is False


def test_stale_page_conflict_is_detected(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    db.update_status(cid, "Under Review", db_path=repo)
    with pytest.raises(db.ConflictError):
        db.update_status(cid, "In Progress", db_path=repo, expected_status="Submitted")
    assert db.update_status(cid, "In Progress", db_path=repo, expected_status="Under Review")


def test_note_is_validated(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    with pytest.raises(ValueError):
        db.update_status(cid, "Under Review", "x" * (config.NOTE_MAX + 1), db_path=repo)
    assert db.get_complaint(cid, repo)["status"] == "Submitted"


def test_updated_at_changes_with_status(repo):
    cid = db.insert_complaint({**REC, "created_at": "2026-01-01T00:00:00+00:00"}, db_path=repo)
    db.update_status(cid, "Under Review", db_path=repo)
    row = db.get_complaint(cid, repo)
    assert row["updated_at"] > row["created_at"]


def test_idempotent_insert_by_workflow_id(repo):
    a = db.insert_complaint(REC, db_path=repo, workflow_id="wf-1")
    b = db.insert_complaint(REC, db_path=repo, workflow_id="wf-1")
    assert a == b and db.get_stats(db_path=repo)["total"] == 1


def test_complaint_id_format(repo):
    import re
    assert re.fullmatch(r"CF-\d{8}-[0-9A-F]{4}", db.insert_complaint(REC, db_path=repo))


def test_legacy_sqlite_database_is_upgraded_without_data_loss(tmp_path):
    """A database created by the ORIGINAL schema keeps all rows and gains history/columns/indexes."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE complaints (complaint_id TEXT PRIMARY KEY, description TEXT NOT NULL, category TEXT NOT NULL,
            location TEXT NOT NULL, priority TEXT NOT NULL, department TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Submitted', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            summary TEXT, draft TEXT, priority_reason TEXT, image_path TEXT);
        CREATE TABLE agent_activity (id INTEGER PRIMARY KEY AUTOINCREMENT, complaint_id TEXT, timestamp TEXT NOT NULL,
            agent TEXT NOT NULL, step TEXT NOT NULL, decision TEXT, reason TEXT, outcome TEXT);
        INSERT INTO complaints VALUES ('CF-20260101-AAAA','Old pothole report on Ring Road','pothole','Ring Road','High',
            'Public Works','In Progress','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00','s','d','r',NULL);
        INSERT INTO agent_activity (complaint_id,timestamp,agent,step,decision,reason,outcome)
            VALUES ('CF-20260101-AAAA','2026-01-01T00:00:00+00:00','Intake Agent','classify','pothole','kw','ok');
    """)
    conn.commit(); conn.close()
    backend = SQLiteBackend(str(path))
    backend.init()
    backend.init()                                                            # idempotent
    row = backend.get_complaint("CF-20260101-AAAA")
    assert row["status"] == "In Progress" and row["description"].startswith("Old pothole")
    assert [h["to_status"] for h in backend.get_status_history("CF-20260101-AAAA")] == ["In Progress"]
    f = {"statuses": None, "categories": None, "priorities": None, "tokens": ["ring"], "created_from": None,
         "created_to_exclusive": None}
    assert backend.search_complaints(f)["total"] == 1
    assert backend.update_status("CF-20260101-AAAA", "Resolved", "done", "tester") is True


def test_two_instances_see_each_others_writes(tmp_path):
    p = str(tmp_path / "shared.db")
    a, b = SQLiteBackend(p), SQLiteBackend(p)
    a.init()
    cid = a.insert_complaint(REC)
    assert b.get_complaint(cid) is not None
