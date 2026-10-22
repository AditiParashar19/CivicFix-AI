from datetime import datetime, timedelta, timezone

from agents.followup_agent import draft_message, evaluate_followups, record_followups
from database import db

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def mk(cid, status="Submitted", priority="Low", created=5, updated=None):
    u = created if updated is None else updated
    return {"complaint_id": cid, "status": status, "priority": priority, "category": "pothole",
            "department": "Public Works Department (Roads)",
            "created_at": (NOW - timedelta(days=created)).isoformat(),
            "updated_at": (NOW - timedelta(days=u)).isoformat()}


def action(decisions, cid):
    return next(d["action"] for d in decisions if d["complaint_id"] == cid)


def test_recent_report_needs_nothing():
    assert action(evaluate_followups([mk("a", created=1)], 3, NOW), "a") == "none"


def test_stale_low_priority_gets_reminder():
    assert action(evaluate_followups([mk("a", created=4)], 3, NOW), "a") == "remind"


def test_stale_high_priority_is_escalated():
    assert action(evaluate_followups([mk("a", priority="High", created=4)], 3, NOW), "a") == "escalate"


def test_very_stale_is_escalated_regardless_of_priority():
    assert action(evaluate_followups([mk("a", created=7)], 3, NOW), "a") == "escalate"


def test_resolved_is_ignored():
    assert evaluate_followups([mk("a", status="Resolved", created=30)], 3, NOW) == []


def test_recent_update_resets_staleness():
    assert action(evaluate_followups([mk("a", created=20, updated=1)], 3, NOW), "a") == "none"


def test_threshold_is_configurable():
    c = [mk("a", created=4)]
    assert action(evaluate_followups(c, 3, NOW), "a") == "remind"
    assert action(evaluate_followups(c, 10, NOW), "a") == "none"


def test_sorted_most_urgent_first_and_marked_simulated():
    ds = evaluate_followups([mk("low", created=1), mk("esc", created=9), mk("rem", created=4)], 3, NOW)
    assert [d["complaint_id"] for d in ds] == ["esc", "rem", "low"]
    assert all(d["simulated"] for d in ds)


def test_messages_are_labelled_simulated():
    d = evaluate_followups([mk("a", created=9)], 3, NOW)[0]
    assert "NOT sent" in draft_message(d)


def test_record_followups_logs_only_due_items(tmp_path):
    p = str(tmp_path / "f.db")
    db.init_db(p)
    ds = evaluate_followups([mk("a", created=9), mk("b", created=1)], 3, NOW)
    assert record_followups(ds, db_path=p) == 1
    assert "Simulated" in db.get_activity(db_path=p)[0]["outcome"]


def test_decision_separates_recommendation_from_persisted_status():
    d = evaluate_followups([mk("a", status="In Progress", created=9)], 3, NOW)[0]
    assert d["recommendation"] == "Recommended escalation" and d["persisted_status"] == "In Progress"
    assert "simulated" in d["status_note"] and d["simulated"] is True
    assert evaluate_followups([mk("b", created=4)], 3, NOW)[0]["recommendation"] == "Recommended reminder"
    assert evaluate_followups([mk("c", created=1)], 3, NOW)[0]["recommendation"] == "No action recommended"


def test_followup_never_changes_status(tmp_path):
    p = str(tmp_path / "n.db")
    db.init_db(p)
    cid = db.insert_complaint({"description": "Old pothole on Ring Road for weeks", "category": "pothole",
                               "location": "Ring Road", "priority": "High", "department": "PW",
                               "created_at": "2026-01-01T00:00:00+00:00"}, db_path=p)
    ds = evaluate_followups(db.list_complaints(db_path=p), 3, NOW)
    record_followups(ds, db_path=p)
    assert db.get_complaint(cid, p)["status"] == "Submitted"


def test_invalid_threshold_and_bad_timestamps_are_handled():
    import pytest
    for bad in (0, -1, None):
        with pytest.raises(ValueError):
            evaluate_followups([mk("a")], bad, NOW)
    broken = {**mk("x"), "created_at": "not-a-date", "updated_at": None}
    d = evaluate_followups([broken, mk("ok", created=9)], 3, NOW)
    assert {x["complaint_id"]: x["action"] for x in d} == {"x": "none", "ok": "escalate"}
    assert next(x for x in d if x["complaint_id"] == "x")["error"] is True


def test_future_timestamps_never_give_negative_ages():
    c = {**mk("a"), "created_at": (NOW + timedelta(days=2)).isoformat(), "updated_at": (NOW + timedelta(days=2)).isoformat()}
    d = evaluate_followups([c], 3, NOW)[0]
    assert d["age_days"] == 0 and d["stale_days"] == 0 and d["action"] == "none"


def test_recording_is_idempotent_across_reruns(tmp_path):
    p = str(tmp_path / "r.db")
    db.init_db(p)
    ds = evaluate_followups([mk("a", created=9), mk("b", created=4)], 3, NOW)
    assert record_followups(ds, db_path=p) == 2
    assert record_followups(ds, db_path=p) == 0                      # same click again: nothing new
    assert db.count_activity(db_path=p, step=["reminder_drafted", "escalation_drafted"]) == 2
