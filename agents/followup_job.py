"""One-shot scheduled follow-up evaluator. It records recommendations, never messages or status changes.

Run periodically from an external scheduler, for example:
    python -m agents.followup_job --threshold-days 3
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

import config
from agents.followup_agent import evaluate_followups, record_followups
from database import db

MAX_REPORTS_PER_RUN = 5000


def run_followup_job(threshold_days: float = config.FOLLOWUP_THRESHOLD_DAYS,
                     db_path: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """Evaluate a bounded set of open complaints and idempotently log due recommendations."""
    if threshold_days <= 0:
        raise ValueError("threshold_days must be positive")
    db.init_db(db_path)
    complaints = db.list_open(limit=MAX_REPORTS_PER_RUN, db_path=db_path)
    decisions = evaluate_followups(complaints, threshold_days, now=now)
    due = [d for d in decisions if d["action"] != "none" and not d.get("error")]
    recorded = record_followups(decisions, db_path=db_path)
    return {
        "evaluated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "reports_checked": len(complaints),
        "limit_reached": len(complaints) >= MAX_REPORTS_PER_RUN,
        "recommendations_due": len(due),
        "reminders_recommended": sum(d["action"] == "remind" for d in due),
        "escalations_recommended": sum(d["action"] == "escalate" for d in due),
        "new_activity_events": recorded,
        "simulated": True,
        "sent": False,
        "statuses_changed": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold-days", type=float, default=config.FOLLOWUP_THRESHOLD_DAYS)
    args = parser.parse_args(argv)
    try:
        result = run_followup_job(args.threshold_days)
    except (ValueError, db.DatabaseError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "sent": False, "statuses_changed": False}),
              file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
