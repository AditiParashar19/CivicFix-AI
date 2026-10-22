"""Copy complaints, status history and activity from a SQLite file into the configured MongoDB.

    python -m database.migrate --from data/civicfix.db --dry-run      # report what would happen
    python -m database.migrate --from data/civicfix.db                # copy (needs MONGODB_URI)

Safe by design: the source is opened read-only; the target is only ADDED to (documents are keyed by
complaint_id and existing ones are skipped, so the command can be re-run); nothing is ever deleted
or overwritten on either side. Demo-flagged records are copied only with --include-demo.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import config
from database import _common as c
from database.mongo_backend import MongoBackend


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def migrate(source: str, target: MongoBackend, dry_run: bool = False, include_demo: bool = False) -> dict:
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(f"SQLite file not found: {src}")
    conn = sqlite3.connect(f"file:{src.resolve()}?mode=ro", uri=True)     # read-only
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(complaints)")}
    has_demo = "is_demo" in cols
    complaints = _rows(conn, "SELECT * FROM complaints ORDER BY created_at")
    summary = {"complaints_found": len(complaints), "copied": 0, "skipped_existing": 0, "skipped_demo": 0,
               "events_copied": 0, "dry_run": dry_run}
    if not dry_run:
        target.init()
    coll, act = (None, None) if dry_run else (target._complaints, target._activity)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for row in complaints:
        if has_demo and row.get("is_demo") and not include_demo:
            summary["skipped_demo"] += 1
            continue
        cid = row["complaint_id"]
        if not dry_run and coll.find_one({"complaint_id": cid}, {"_id": 1}):
            summary["skipped_existing"] += 1
            continue
        if "status_history" in tables:
            hist = [{"from_status": h["from_status"], "to_status": h["to_status"], "note": h["note"] or "",
                     "actor": h["actor"] or "", "changed_at": h["changed_at"]}
                    for h in _rows(conn, "SELECT * FROM status_history WHERE complaint_id=? ORDER BY changed_at, id", (cid,))]
        else:
            hist = []
        if not hist:
            hist = [{"from_status": None, "to_status": row["status"], "note": "Migrated from SQLite",
                     "actor": "System", "changed_at": row["created_at"]}]
        events = [c.build_event(cid, e) for e in
                  _rows(conn, "SELECT timestamp, agent, step, decision, reason, outcome FROM agent_activity "
                              "WHERE complaint_id=? ORDER BY timestamp, id", (cid,))]
        summary["copied"] += 1
        summary["events_copied"] += len(events)
        if dry_run:
            continue
        doc = {k: row.get(k) for k in c.COMPLAINT_COLUMNS}
        doc["owner_id"] = row.get("owner_id") if "owner_id" in cols else None
        doc.update(priority_rank=c.PRIORITY_RANK.get(row["priority"], 0), is_demo=bool(row.get("is_demo", 0)),
                   status_history=hist)
        if row.get("workflow_id"):
            doc["workflow_id"] = row["workflow_id"]
        coll.insert_one(doc)
        if events:
            act.insert_many(events, ordered=False)
    conn.close()
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="source", default=config.DB_PATH, help="SQLite file (default: CIVICFIX_DB_PATH)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--include-demo", action="store_true")
    args = p.parse_args(argv)
    if not args.dry_run and not config.MONGODB_URI:
        print("MONGODB_URI is not set. Set it in .env (see .env.example) or use --dry-run.", file=sys.stderr)
        return 2
    try:
        result = migrate(args.source, MongoBackend(), args.dry_run, args.include_demo)
    except (FileNotFoundError, c.DatabaseError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    for k, v in result.items():
        print(f"{k:>18}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
