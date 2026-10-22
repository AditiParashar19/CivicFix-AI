"""SQLite repository (development / offline demo). All SQL is parameterised.

Existing databases are upgraded in place by ``init()``: new columns and tables are ADDED, nothing is
dropped or rewritten, and existing complaints receive one backfilled status-history entry.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import config
from database import _common as c
from models import utc_now

_STATUS_LIST = ", ".join(f"'{s}'" for s in config.STATUSES)   # constants only, never user input

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id   TEXT PRIMARY KEY,
    description    TEXT NOT NULL,
    category       TEXT NOT NULL,
    location       TEXT NOT NULL,
    priority       TEXT NOT NULL,
    department     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'Submitted' CHECK (status IN ({_STATUS_LIST})),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    summary        TEXT,
    draft          TEXT,
    priority_reason TEXT,
    image_path     TEXT,
    owner_id       TEXT
);
CREATE TABLE IF NOT EXISTS agent_activity (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id TEXT,
    timestamp    TEXT NOT NULL,
    agent        TEXT NOT NULL,
    step         TEXT NOT NULL,
    decision     TEXT,
    reason       TEXT,
    outcome      TEXT
);
CREATE TABLE IF NOT EXISTS status_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    note         TEXT,
    actor        TEXT,
    changed_at   TEXT NOT NULL
);
"""

# index name -> DDL, created AFTER column migrations so old databases are upgraded safely
INDEXES = {
    "idx_activity_complaint": "CREATE INDEX IF NOT EXISTS idx_activity_complaint ON agent_activity(complaint_id)",
    "idx_activity_time": "CREATE INDEX IF NOT EXISTS idx_activity_time ON agent_activity(timestamp)",
    "idx_activity_agent": "CREATE INDEX IF NOT EXISTS idx_activity_agent ON agent_activity(agent)",
    "idx_activity_dedupe": "CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_dedupe ON agent_activity(dedupe_key) "
                           "WHERE dedupe_key IS NOT NULL",
    "idx_complaints_status": "CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status)",
    "idx_complaints_category": "CREATE INDEX IF NOT EXISTS idx_complaints_category ON complaints(category)",
    "idx_complaints_priority": "CREATE INDEX IF NOT EXISTS idx_complaints_priority ON complaints(priority)",
    "idx_complaints_created": "CREATE INDEX IF NOT EXISTS idx_complaints_created ON complaints(created_at)",
    "idx_complaints_owner": "CREATE INDEX IF NOT EXISTS idx_complaints_owner ON complaints(owner_id, created_at)",
    "idx_complaints_workflow": "CREATE UNIQUE INDEX IF NOT EXISTS idx_complaints_workflow ON complaints(workflow_id) "
                               "WHERE workflow_id IS NOT NULL",
    "idx_history_complaint": "CREATE INDEX IF NOT EXISTS idx_history_complaint ON status_history(complaint_id)",
}
ADDED_COLUMNS = {"complaints": {"workflow_id": "TEXT", "is_demo": "INTEGER NOT NULL DEFAULT 0", "owner_id": "TEXT"},
                 "agent_activity": {"dedupe_key": "TEXT"}}
_PRIORITY_CASE = "CASE priority " + " ".join(f"WHEN '{p}' THEN {i}" for p, i in c.PRIORITY_RANK.items()) + " ELSE -1 END"
_SORT_SQL = {"newest": "created_at DESC, complaint_id", "oldest": "created_at ASC, complaint_id",
             "updated": "updated_at DESC, complaint_id", "priority": f"{_PRIORITY_CASE} DESC, created_at DESC, complaint_id"}


def _like(token: str) -> str:
    return "%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SQLiteBackend:
    kind = "sqlite"

    def __init__(self, path: Optional[str] = None):
        self.path = str(path or config.DB_PATH)

    # ---- plumbing ---------------------------------------------------------------------------
    @contextmanager
    def _conn(self, write: bool = False):
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        except sqlite3.Error as exc:
            raise c.DatabaseError(f"Cannot open the SQLite database: {exc}") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback(conn)
            raise c.DatabaseError(f"Database operation failed: {exc}") from exc
        except BaseException:
            self._rollback(conn)
            raise
        finally:
            conn.close()

    @staticmethod
    def _rollback(conn) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def describe(self) -> dict:
        return {"kind": "sqlite", "label": "local SQLite file", "persistent": True, "shared": False,
                "detail": self.path if self.path == ":memory:" else Path(self.path).name}

    def health(self) -> dict:
        with self._conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True, **self.describe()}

    # ---- schema / lifecycle -----------------------------------------------------------------
    def init(self) -> None:
        with self._conn(write=True) as conn:
            for stmt in [s for s in SCHEMA.split(";") if s.strip()]:
                conn.execute(stmt)
            for table, cols in ADDED_COLUMNS.items():
                existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                for col, ddl in cols.items():                      # names come from the constant above
                    if col not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            for ddl in INDEXES.values():
                conn.execute(ddl)
            # Backfill ONE history entry for complaints that predate status history (idempotent).
            conn.execute(
                "INSERT INTO status_history (complaint_id, from_status, to_status, note, actor, changed_at) "
                "SELECT complaint_id, NULL, status, 'Backfilled from an existing record', 'System', created_at "
                "FROM complaints WHERE complaint_id NOT IN (SELECT complaint_id FROM status_history)")

    def wipe_all(self) -> None:
        """Delete EVERYTHING (explicit, destructive). Callers must have obtained confirmation."""
        with self._conn(write=True) as conn:
            for t in ("agent_activity", "status_history", "complaints"):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        self.init()

    def delete_demo(self) -> int:
        with self._conn(write=True) as conn:
            ids = [r[0] for r in conn.execute("SELECT complaint_id FROM complaints WHERE is_demo = 1")]
            for cid in ids:
                conn.execute("DELETE FROM agent_activity WHERE complaint_id = ?", (cid,))
                conn.execute("DELETE FROM status_history WHERE complaint_id = ?", (cid,))
                conn.execute("DELETE FROM complaints WHERE complaint_id = ?", (cid,))
        return len(ids)

    # ---- complaints -------------------------------------------------------------------------
    @staticmethod
    def _insert_event(conn, ev: dict) -> bool:
        cur = conn.execute(
            "INSERT OR IGNORE INTO agent_activity (complaint_id, timestamp, agent, step, decision, reason, outcome, dedupe_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ev["complaint_id"], ev["timestamp"], ev["agent"], ev["step"], ev["decision"], ev["reason"],
             ev["outcome"], ev.get("dedupe_key")))
        return cur.rowcount > 0

    def insert_complaint(self, record: dict, trace: Optional[list] = None, workflow_id: Optional[str] = None,
                         is_demo: bool = False) -> str:
        doc = c.build_complaint(record, workflow_id, is_demo)
        with self._conn(write=True) as conn:
            if workflow_id:                                  # idempotent: a re-run of the same save returns the same ID
                row = conn.execute("SELECT complaint_id FROM complaints WHERE workflow_id = ?", (workflow_id,)).fetchone()
                if row:
                    return row[0]
            for _ in range(20):
                cid = c.new_complaint_id()
                if conn.execute("SELECT 1 FROM complaints WHERE complaint_id = ?", (cid,)).fetchone() is None:
                    break
            else:
                raise c.DatabaseError("Could not allocate a unique complaint ID.")
            conn.execute(
                "INSERT INTO complaints (complaint_id, description, category, location, priority, department, status, "
                "created_at, updated_at, summary, draft, priority_reason, image_path, workflow_id, is_demo, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, doc["description"], doc["category"], doc["location"], doc["priority"], doc["department"],
                 doc["status"], doc["created_at"], doc["updated_at"], doc["summary"], doc["draft"],
                 doc["priority_reason"], doc["image_path"], workflow_id, int(doc["is_demo"]), doc["owner_id"]))
            conn.execute("INSERT INTO status_history (complaint_id, from_status, to_status, note, actor, changed_at) "
                         "VALUES (?, NULL, ?, ?, ?, ?)",
                         (cid, doc["status"], "Report created" if not is_demo else "Fictional sample record loaded",
                          "Citizen (confirmed)" if not is_demo else "Seed", doc["created_at"]))
            for step in trace or []:
                self._insert_event(conn, c.build_event(cid, step))
        return cid

    def get_complaint(self, complaint_id: str, owner_id: Optional[str] = None) -> Optional[dict]:
        with self._conn() as conn:
            if owner_id:
                row = conn.execute("SELECT * FROM complaints WHERE complaint_id = ? AND owner_id = ?",
                                   (complaint_id, owner_id)).fetchone()
            else:
                row = conn.execute("SELECT * FROM complaints WHERE complaint_id = ?", (complaint_id,)).fetchone()
        return self._public(row)

    def get_complaint_id_by_workflow(self, workflow_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT complaint_id FROM complaints WHERE workflow_id = ?", (workflow_id,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _public(row) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        d.pop("workflow_id", None)
        d["is_demo"] = bool(d.get("is_demo"))
        return d

    @staticmethod
    def _where(f: dict) -> tuple[str, list]:
        sql, params = " WHERE 1=1", []
        for col, key in (("status", "statuses"), ("category", "categories"), ("priority", "priorities")):
            if f[key]:
                sql += f" AND {col} IN ({','.join('?' * len(f[key]))})"    # col from fixed tuple
                params += f[key]
        if f["created_from"]:
            sql += " AND created_at >= ?"
            params.append(f["created_from"])
        if f["created_to_exclusive"]:
            sql += " AND created_at < ?"
            params.append(f["created_to_exclusive"])
        if f.get("owner_id"):
            sql += " AND owner_id = ?"
            params.append(f["owner_id"])
        for tok in f["tokens"]:
            sql += " AND (" + " OR ".join(f"{fld} LIKE ? ESCAPE '\\'" for fld in c.SEARCH_FIELDS) + ")"
            params += [_like(tok)] * len(c.SEARCH_FIELDS)
        return sql, params

    def search_complaints(self, filters: dict, sort: str = "newest", page: int = 1,
                          page_size: int = config.PAGE_SIZE_DEFAULT) -> dict:
        if sort not in _SORT_SQL:
            raise ValueError(f"Invalid sort: {sort!r}")
        where, params = self._where(filters)
        size, offset = c.page_bounds(page, page_size)
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM complaints" + where, params).fetchone()[0]
            result = c.page_result([], total, page, page_size)
            offset = (result["page"] - 1) * size
            rows = conn.execute(f"SELECT * FROM complaints{where} ORDER BY {_SORT_SQL[sort]} LIMIT ? OFFSET ?",
                                params + [size, offset]).fetchall()
        result["rows"] = [self._public(r) for r in rows]
        return result

    def count_by(self, field: str, filters: dict) -> dict:
        if field not in c.COUNT_FIELDS:
            raise ValueError(f"Cannot count by {field!r}")
        where, params = self._where(filters)
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {field} AS k, COUNT(*) AS n FROM complaints{where} GROUP BY {field}", params).fetchall()
        return {r["k"]: r["n"] for r in rows}

    def set_image_path(self, complaint_id: str, image_path: str) -> None:
        with self._conn(write=True) as conn:
            conn.execute("UPDATE complaints SET image_path = ? WHERE complaint_id = ?", (image_path, complaint_id))

    def delete_complaint(self, complaint_id: str) -> bool:
        with self._conn(write=True) as conn:
            cur = conn.execute("DELETE FROM complaints WHERE complaint_id = ?", (complaint_id,))
            conn.execute("DELETE FROM agent_activity WHERE complaint_id = ?", (complaint_id,))
            conn.execute("DELETE FROM status_history WHERE complaint_id = ?", (complaint_id,))
            return cur.rowcount > 0

    # ---- status -----------------------------------------------------------------------------
    def update_status(self, complaint_id: str, new_status: str, note: str, actor: str,
                      expected_status: Optional[str] = None) -> bool:
        note = c.clean_note(note)
        with self._conn(write=True) as conn:
            row = conn.execute("SELECT status FROM complaints WHERE complaint_id = ?", (complaint_id,)).fetchone()
            if row is None:
                return False
            old = row["status"]
            if expected_status and expected_status != old:
                raise c.ConflictError(f"The status changed to '{old}' since this page was loaded. Reload and retry.")
            c.check_transition(old, new_status)
            now = utc_now()
            conn.execute("UPDATE complaints SET status = ?, updated_at = ? WHERE complaint_id = ?",
                         (new_status, now, complaint_id))
            conn.execute("INSERT INTO status_history (complaint_id, from_status, to_status, note, actor, changed_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (complaint_id, old, new_status, note, actor, now))
            self._insert_event(conn, c.build_event(complaint_id, {
                "timestamp": now, "agent": "Status Update", "step": "status_change",
                "decision": f"{old} -> {new_status}",
                "reason": note or "Status changed manually in the prototype UI",
                "outcome": f"Recorded by {actor}. Simulated change; no real department was contacted."}))
        return True

    def get_status_history(self, complaint_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT from_status, to_status, note, actor, changed_at FROM status_history "
                                "WHERE complaint_id = ? ORDER BY changed_at, id", (complaint_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---- activity ---------------------------------------------------------------------------
    def log_activity(self, complaint_id: Optional[str], event: dict) -> bool:
        with self._conn(write=True) as conn:
            return self._insert_event(conn, c.build_event(complaint_id, event))

    @staticmethod
    def _activity_where(f: dict) -> tuple[str, list]:
        sql, params = " WHERE 1=1", []
        if f["complaint_id"]:
            sql += " AND complaint_id = ?"
            params.append(f["complaint_id"])
        if f["complaint_id_contains"]:
            sql += " AND complaint_id LIKE ? ESCAPE '\\'"
            params.append(_like(f["complaint_id_contains"]))
        for col, key in (("agent", "agents"), ("step", "steps")):
            if f[key]:
                sql += f" AND {col} IN ({','.join('?' * len(f[key]))})"
                params += f[key]
        if f["date_from"]:
            sql += " AND timestamp >= ?"
            params.append(f["date_from"])
        if f["date_to_exclusive"]:
            sql += " AND timestamp < ?"
            params.append(f["date_to_exclusive"])
        for tok in f["outcome_tokens"]:
            sql += " AND outcome LIKE ? ESCAPE '\\'"
            params.append(_like(tok))
        for tok in f["text_tokens"]:
            sql += " AND (" + " OR ".join(f"{fld} LIKE ? ESCAPE '\\'" for fld in c.ACTIVITY_TEXT_FIELDS) + ")"
            params += [_like(tok)] * len(c.ACTIVITY_TEXT_FIELDS)
        return sql, params

    def get_activity(self, filters: dict, newest_first: bool, limit: int, offset: int) -> list[dict]:
        where, params = self._activity_where(filters)
        order = "DESC" if newest_first else "ASC"
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id, complaint_id, timestamp, agent, step, decision, reason, outcome FROM agent_activity{where} "
                f"ORDER BY timestamp {order}, id {order} LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]

    def count_activity(self, filters: dict) -> int:
        where, params = self._activity_where(filters)
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM agent_activity" + where, params).fetchone()[0]

    def distinct_activity(self, field: str) -> list[str]:
        if field not in ("agent", "step"):
            raise ValueError(f"Cannot list values of {field!r}")
        with self._conn() as conn:
            return [r[0] for r in conn.execute(f"SELECT DISTINCT {field} FROM agent_activity ORDER BY {field}")]
