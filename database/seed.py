"""Sample data for demonstrations. All records are fictional.

    python -m database.seed                    # add samples ONLY if the database is empty
    python -m database.seed --reset            # remove previous demo records (is_demo) and reseed; real data is kept
    python -m database.seed --wipe-all --yes   # DESTRUCTIVE: delete every record, then reseed

Seeded records are flagged ``is_demo`` so they can be removed without touching real reports.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from agents.complaint_agent import analyze_complaint
from agents.planning_agent import plan_resolution
from database import db
from models import trace_step


def seed_database(reset: bool = False, db_path: Optional[str] = None,
                  now: Optional[datetime] = None) -> int:
    """Insert the sample reports (through the real Intake + Planning agents). Returns how many were added.

    ``reset=True`` removes earlier DEMO records only (real reports are never deleted) before reseeding.
    Without ``reset`` nothing is added if the database already holds any report.
    """
    db.init_db(db_path)
    if reset:
        db.delete_demo_data(db_path)
    elif db.get_stats(db_path)["total"] > 0:
        return 0
    now = now or datetime.now(timezone.utc)
    samples = json.loads(config.SAMPLE_DATA_PATH.read_text(encoding="utf-8"))
    for s in samples:
        a = analyze_complaint(s["description"], s["location"], use_llm=False)
        plan = plan_resolution(a, None)
        created = (now - timedelta(days=s["days_ago"])).replace(microsecond=0).isoformat()
        updated = (now - timedelta(days=s["updated_days_ago"])).replace(microsecond=0).isoformat()
        trace = a["trace"] + plan["trace"] + [trace_step(
            "Seed", "load_sample", "Sample record loaded", "Fictional demo data",
            f"Status set to '{s['status']}' (simulated).")]
        db.insert_complaint({
            "description": a["description"], "category": a["category"], "location": a["location"],
            "priority": a["priority"], "department": plan["department"], "status": s["status"],
            "summary": a["summary"], "draft": a["draft"], "priority_reason": a["priority_reason"],
            "created_at": created, "updated_at": updated}, trace=trace, db_path=db_path,
            is_demo=True)
    return len(samples)


if __name__ == "__main__":
    if "--wipe-all" in sys.argv:
        if "--yes" not in sys.argv:
            sys.exit("Refusing to delete all data without --yes.")
        db.reset_db()
    n = seed_database(reset="--reset" in sys.argv or "--wipe-all" in sys.argv)
    print(f"Seeded {n} fictional sample complaints into {db.describe_backend()['label']}")
