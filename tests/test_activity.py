"""Activity log: structured events, filters, pagination, dedupe, attribution, failure visibility."""
import pytest

import agents.orchestrator as orch
from conftest import REC
from database import db


def test_log_and_retrieve_structured_event(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    assert db.log_activity(cid, "Intake Agent", "classify", "Category = pothole", "keywords", "ok", db_path=repo) is True
    ev = db.get_activity(cid, db_path=repo)[-1]
    assert {"timestamp", "agent", "step", "decision", "reason", "outcome", "complaint_id"} <= set(ev)
    assert (ev["agent"], ev["step"], ev["complaint_id"]) == ("Intake Agent", "classify", cid)


def test_filters_agent_step_outcome_text_complaint(repo):
    a, b = (db.insert_complaint(REC, db_path=repo) for _ in range(2))
    db.log_activity(a, "Intake Agent", "classify", "x", "keyword pothole", "Confidence 0.9", db_path=repo)
    db.log_activity(b, "Duplicate Agent", "compare", "none", "tfidf", "Report treated as new", db_path=repo)
    db.log_activity(None, "Orchestrator", "intake_failed", "fallback", "ValueError: boom", "continues", db_path=repo)
    assert {e["agent"] for e in db.get_activity(agent="Duplicate Agent", db_path=repo)} == {"Duplicate Agent"}
    assert [e["step"] for e in db.get_activity(step="intake_failed", db_path=repo)] == ["intake_failed"]
    assert len(db.get_activity(outcome_contains="treated as new", db_path=repo)) == 1
    assert len(db.get_activity(text="BOOM", db_path=repo)) == 1
    assert {e["complaint_id"] for e in db.get_activity(a, db_path=repo)} == {a}
    assert len(db.get_activity(complaint_id_contains=a[-4:], db_path=repo)) >= 1
    assert db.count_activity(agent=["Intake Agent", "Duplicate Agent"], db_path=repo) == 2
    assert {"Intake Agent", "Duplicate Agent", "Orchestrator"} <= set(db.distinct_activity_values("agent", db_path=repo))


def test_date_range_and_invalid_range(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    db.log_activity(cid, "A", "s", db_path=repo)
    today = db.get_activity(db_path=repo)[0]["timestamp"][:10]
    assert db.count_activity(date_from=today, date_to=today, db_path=repo) >= 1
    assert db.count_activity(date_to="2000-01-01", db_path=repo) == 0
    with pytest.raises(ValueError):
        db.get_activity(date_from="2026-05-02", date_to="2026-05-01", db_path=repo)


def test_ordering_and_pagination(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    for i in range(5):
        db.log_activity(cid, "A", f"s{i}", db_path=repo)
    chrono = [e["step"] for e in db.get_activity(cid, db_path=repo)]
    assert chrono[-5:] == [f"s{i}" for i in range(5)]                       # timeline is oldest-first
    newest = [e["step"] for e in db.get_activity(db_path=repo, limit=2)]
    assert newest == ["s4", "s3"]                                           # explorer is newest-first
    page2 = [e["step"] for e in db.get_activity(db_path=repo, limit=2, offset=2)]
    assert page2 == ["s2", "s1"]


def test_dedupe_key_prevents_duplicate_events_on_rerun(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    assert db.log_activity(cid, "F", "reminder", dedupe_key="k1", db_path=repo) is True
    assert db.log_activity(cid, "F", "reminder", dedupe_key="k1", db_path=repo) is False
    assert db.log_activity(cid, "F", "reminder", dedupe_key="k2", db_path=repo) is True
    assert db.count_activity(cid, db_path=repo, step="reminder") == 2


def test_status_change_creates_event_and_history_together(repo):
    cid = db.insert_complaint(REC, db_path=repo)
    db.update_status(cid, "Under Review", "assigned to a reviewer", db_path=repo)
    ev = [e for e in db.get_activity(cid, db_path=repo) if e["step"] == "status_change"]
    assert ev[-1]["decision"] == "Submitted -> Under Review" and "Simulated" in ev[-1]["outcome"]
    hist = db.get_status_history(cid, repo)
    assert [(h["from_status"], h["to_status"]) for h in hist] == [(None, "Submitted"), ("Submitted", "Under Review")]
    assert hist[-1]["note"] == "assigned to a reviewer"


def test_logging_failure_raises_instead_of_being_swallowed(tmp_path):
    with pytest.raises(db.DatabaseError):
        db.log_activity(None, "A", "s", db_path=str(tmp_path))              # unwritable target


def test_events_do_not_contain_credentials(repo, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-SECRET-123")
    w = orch.run_intake_workflow("Large pothole on Sector 4 Main Road near the bus stop", "Sector 4", db_path=repo)
    cid = orch.save_report(w, db_path=repo)
    assert all("SECRET" not in str(v) for e in db.get_activity(cid, db_path=repo) for v in e.values())


def test_saved_events_belong_to_the_saved_complaint_and_drafts_write_nothing(repo):
    w = orch.run_intake_workflow("Large pothole on Sector 4 Main Road near the bus stop", "Sector 4", use_llm=False,
                                 db_path=repo)
    assert w["ok"] and db.count_activity(db_path=repo) == 0 and db.get_stats(db_path=repo)["total"] == 0
    cid = orch.save_report(w, db_path=repo)
    events = db.get_activity(cid, db_path=repo)
    assert len(events) == len(w["trace"]) + 1                                # trace + confirm_save
    assert db.count_activity(db_path=repo) == len(events)                    # nothing unattached
    assert {"Human Review", "Orchestrator", "Intake Agent"} <= {e["agent"] for e in events}


def test_failed_agent_stage_is_explained_and_persisted_unattached(repo, monkeypatch):
    real = orch.analyze_complaint

    def flaky(desc, loc, hint=None, use_llm=True):
        if use_llm is False:
            return real(desc, loc, hint, use_llm=False)
        raise RuntimeError("model exploded")
    monkeypatch.setattr(orch, "analyze_complaint", flaky)
    w = orch.run_intake_workflow("Large pothole on Sector 4 Main Road near the bus stop", "Sector 4", db_path=repo)
    assert w["ok"] and any(t["step"] == "intake_failed" and "model exploded" in t["reason"] for t in w["trace"])
    unattached = db.get_activity(step="intake_failed", db_path=repo)
    assert len(unattached) == 1 and unattached[0]["complaint_id"] is None
    assert db.get_stats(db_path=repo)["total"] == 0                          # a failure never creates a complaint


def test_activity_survives_restart(tmp_path):
    from database.sqlite_backend import SQLiteBackend
    path = str(tmp_path / "restart.db")
    first = SQLiteBackend(path)
    first.init()
    cid = first.insert_complaint(REC, [{"agent": "A", "step": "s"}])
    reopened = SQLiteBackend(path)                                           # a brand-new process would do the same
    reopened.init()
    assert reopened.get_complaint(cid)["status"] == "Submitted"
    assert reopened.count_activity({"complaint_id": cid, "complaint_id_contains": None, "agents": None, "steps": None,
                                    "outcome_tokens": [], "text_tokens": [], "date_from": None,
                                    "date_to_exclusive": None}) == 1
