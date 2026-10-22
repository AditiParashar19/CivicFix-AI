"""Dashboard metric definitions, demo seed/reset behaviour and planning-agent validation."""
import pytest

import config
from agents.planning_agent import plan_resolution, validate_plan
from conftest import REC, advance
from database import db
from database.seed import seed_database


# ---- metrics ----------------------------------------------------------------------------------------
def test_pending_is_submitted_plus_under_review_and_in_progress_is_separate(repo):
    ids = [db.insert_complaint(REC, db_path=repo) for _ in range(5)]
    advance(repo, ids[1], "Under Review")
    advance(repo, ids[2], "In Progress")
    advance(repo, ids[3], "Resolved")
    s = db.get_stats(db_path=repo)
    assert (s["total"], s["submitted"], s["under_review"], s["in_progress"], s["resolved"]) == (5, 2, 1, 1, 1)
    assert s["pending"] == s["submitted"] + s["under_review"] == 3
    assert s["pending"] + s["in_progress"] + s["resolved"] == s["total"]


def test_config_pending_definition_matches_the_rule():
    assert config.PENDING_STATUSES == ["Submitted", "Under Review"]
    assert config.OPEN_STATUSES == ["Submitted", "Under Review", "In Progress"]


def test_filtered_stats_agree_with_search_totals(repo):
    a = db.insert_complaint(REC, db_path=repo)
    db.insert_complaint({**REC, "category": "garbage"}, db_path=repo)
    advance(repo, a, "In Progress")
    assert db.get_stats(db_path=repo, category="pothole")["total"] == db.search_complaints(category="pothole", db_path=repo)["total"] == 1
    assert db.get_stats(db_path=repo, category="pothole")["in_progress"] == 1
    assert db.get_stats(db_path=repo)["total"] == 2
    assert db.get_stats(db_path=repo, open_only=True)["total"] == 2


def test_empty_database_stats_are_zero(repo):
    assert db.get_stats(db_path=repo) == {"submitted": 0, "under_review": 0, "in_progress": 0, "resolved": 0,
                                          "total": 0, "pending": 0}


# ---- seed / reset -----------------------------------------------------------------------------------
def test_seed_only_fills_an_empty_database_and_flags_demo_records(repo):
    assert seed_database(db_path=repo) == 14
    assert seed_database(db_path=repo) == 0
    assert all(r["is_demo"] for r in db.list_complaints(db_path=repo))
    assert db.get_stats(db_path=repo)["resolved"] == 2


def test_demo_reset_never_deletes_real_reports(repo):
    real = db.insert_complaint(REC, db_path=repo)
    assert seed_database(db_path=repo) == 0                      # real data present, so demo data is NOT mixed in
    seed_database(reset=True, db_path=repo)                      # explicit demo reset adds demo records...
    seed_database(reset=True, db_path=repo)                      # ...and a second reset replaces them, not duplicates
    assert db.get_complaint(real, repo) is not None
    rows = db.list_complaints(db_path=repo)
    assert len(rows) == 15 and sum(r["is_demo"] for r in rows) == 14
    assert db.delete_demo_data(repo) == 14 and db.get_stats(db_path=repo)["total"] == 1


def test_seed_history_and_activity_exist(repo):
    seed_database(db_path=repo)
    cid = db.list_complaints(db_path=repo)[0]["complaint_id"]
    assert db.get_status_history(cid, repo) and db.get_activity(cid, db_path=repo)


def test_full_wipe_is_explicit(repo):
    db.insert_complaint(REC, db_path=repo)
    db.reset_db(repo)
    assert db.get_stats(db_path=repo)["total"] == 0


# ---- planning agent ---------------------------------------------------------------------------------
A = {"category": "pothole", "priority": "Low"}


@pytest.mark.parametrize("priority,sla", [("Low", 7), ("Medium", 7), ("High", 3), ("Critical", 1)])
def test_sla_depends_on_priority_and_is_labelled_simulated(priority, sla):
    p = plan_resolution({**A, "priority": priority}, None)
    assert p["sla_days"] == sla and p["simulated"] and p["requires_human_review"] and p["sla_basis"]
    assert p["contact_email"].endswith(".example") and "No department has received" in p["reason"]


def test_plan_mentions_duplicates_and_validation_rejects_bad_plans():
    dup = {"is_potential_duplicate": True, "matches": [{"complaint_id": "CF-1", "status": "Submitted"}]}
    assert "CF-1" in plan_resolution(A, dup)["duplicate_advice"]
    good = plan_resolution(A, None)
    for bad in ({**good, "requires_human_review": False}, {**good, "simulated": False}, {**good, "sla_days": 0},
                {**good, "contact_email": "mayor@gov.in"}, {**good, "actions": []}):
        with pytest.raises(ValueError):
            validate_plan(bad)


def test_unknown_category_routes_to_general_cell():
    assert plan_resolution({"category": "noise", "priority": "Low"}, None)["department"] == "General Grievance Cell"
