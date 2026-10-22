from datetime import datetime, timedelta, timezone

import pytest

from conftest import REC
from agents.followup_job import run_followup_job
from database import db


def test_scheduled_job_records_idempotent_recommendations_without_side_effects(repo):
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    old = (now - timedelta(days=4)).isoformat()
    cid = db.insert_complaint({**REC, "priority": "Medium", "created_at": old, "updated_at": old}, db_path=repo)

    first = run_followup_job(threshold_days=3, db_path=repo, now=now)
    second = run_followup_job(threshold_days=3, db_path=repo, now=now)

    assert first["reports_checked"] == 1
    assert first["reminders_recommended"] == 1
    assert first["new_activity_events"] == 1
    assert second["new_activity_events"] == 0
    assert first["sent"] is second["sent"] is False
    assert first["statuses_changed"] is second["statuses_changed"] is False
    assert db.get_complaint(cid, db_path=repo)["status"] == "Submitted"
    assert db.count_activity(complaint_id=cid, db_path=repo) == 1


def test_scheduled_job_rejects_nonpositive_threshold(repo):
    with pytest.raises(ValueError, match="positive"):
        run_followup_job(0, db_path=repo)
