"""Repository facade. Agents and pages import THIS module and never touch a database driver.

Backend selection
  * ``db_path=...`` (used by tests and tools)  -> a SQLite file at that path
  * otherwise, ``MONGODB_URI`` set              -> MongoDB (a failed connection is an error, never a silent fallback)
  * otherwise                                   -> local SQLite file (config.DB_PATH): development / offline demo

The public function names and signatures from the original SQLite-only module are preserved; new
functionality (paginated search, counts, status history, activity filters) is additive.
"""
from datetime import date
from typing import Any, Optional, Sequence, Union

import pandas as pd

import config
from database import _common as c
from database.sqlite_backend import SQLiteBackend

DatabaseError, InvalidTransition, ConflictError = c.DatabaseError, c.InvalidTransition, c.ConflictError
REQUIRED_FIELDS = ["description", "category", "location", "priority", "department"]

_cache: dict[str, Any] = {}
Many = Union[str, Sequence[str], None]


def get_backend(db_path: Optional[str] = None):
    """Return the repository for ``db_path`` or for the configured default."""
    if db_path:
        key = f"sqlite:{db_path}"
        if key not in _cache:
            _cache[key] = SQLiteBackend(str(db_path))
        return _cache[key]
    if config.MONGODB_URI:
        if "mongo" not in _cache:
            from database.mongo_backend import MongoBackend
            _cache["mongo"] = MongoBackend()
        return _cache["mongo"]
    key = f"sqlite:{config.DB_PATH}"
    if key not in _cache:
        _cache[key] = SQLiteBackend(config.DB_PATH)
    return _cache[key]


def set_backend(backend, db_path: Optional[str] = None) -> None:
    """Install a specific backend as the default (or for ``db_path``). Used by tests and tooling."""
    if db_path:
        _cache[f"sqlite:{db_path}"] = backend
    else:
        _cache["mongo" if backend.kind == "mongodb" else f"sqlite:{config.DB_PATH}"] = backend


def describe_backend(db_path: Optional[str] = None) -> dict:
    return get_backend(db_path).describe()


def health_check(db_path: Optional[str] = None) -> dict:
    return get_backend(db_path).health()


# ---- lifecycle ---------------------------------------------------------------------------------
def init_db(db_path: Optional[str] = None) -> None:
    """Create/upgrade storage (idempotent, never deletes data)."""
    get_backend(db_path).init()


def reset_db(db_path: Optional[str] = None) -> None:
    """DESTRUCTIVE: delete all complaints, history and activity. Callers must obtain explicit confirmation."""
    get_backend(db_path).wipe_all()


def delete_demo_data(db_path: Optional[str] = None) -> int:
    """Delete only records flagged as fictional demo data. Returns how many complaints were removed."""
    return get_backend(db_path).delete_demo()


# ---- complaints --------------------------------------------------------------------------------
def insert_complaint(record: dict, trace: Optional[list] = None, db_path: Optional[str] = None,
                     workflow_id: Optional[str] = None, is_demo: bool = False) -> str:
    """Insert a complaint (+ optional activity trace) atomically and return its ID (CF-YYYYMMDD-XXXX).

    If ``workflow_id`` was already saved, the existing ID is returned and nothing is written (idempotent).
    """
    return get_backend(db_path).insert_complaint(record, trace, workflow_id, is_demo)


def get_complaint(complaint_id: str, db_path: Optional[str] = None,
                  owner_id: Optional[str] = None) -> Optional[dict]:
    return get_backend(db_path).get_complaint(complaint_id, owner_id)


def get_complaint_id_by_workflow(workflow_id: str, db_path: Optional[str] = None) -> Optional[str]:
    return get_backend(db_path).get_complaint_id_by_workflow(workflow_id)


def search_complaints(status: Many = None, category: Many = None, priority: Many = None,
                      search: Optional[str] = None, created_from: Union[date, str, None] = None,
                      created_to: Union[date, str, None] = None, open_only: bool = False,
                      sort: str = "newest", page: int = 1, page_size: int = config.PAGE_SIZE_DEFAULT,
                      db_path: Optional[str] = None, owner_id: Optional[str] = None) -> dict:
    """Filtered, sorted, paginated search -> {rows, total, page, page_size, pages}.

    Filters combine with AND. ``search`` splits on whitespace; every word must appear (case-insensitive)
    in the ID, description, location, summary, department or category. Invalid filters raise ValueError;
    storage failures raise DatabaseError (never an empty result that looks like "no matches").
    """
    f = c.normalize_filters(status, category, priority, search, created_from, created_to, open_only, owner_id)
    return get_backend(db_path).search_complaints(f, sort, page, page_size)


def list_complaints(status: Many = None, category: Many = None, priority: Many = None,
                    search: Optional[str] = None, db_path: Optional[str] = None, **kw) -> list[dict]:
    """Compatibility wrapper: ALL matching complaints, newest first (capped at 5000). Prefer search_complaints()."""
    f = c.normalize_filters(status, category, priority, search, kw.get("created_from"), kw.get("created_to"),
                            kw.get("open_only", False), kw.get("owner_id"))
    backend, out, page = get_backend(db_path), [], 1
    while len(out) < 5000:
        res = backend.search_complaints(f, kw.get("sort", "newest"), page, config.PAGE_SIZE_MAX)
        out += res["rows"]
        if page >= res["pages"]:
            break
        page += 1
    return out


def list_open(limit: int = 1000, db_path: Optional[str] = None) -> list[dict]:
    """Unresolved complaints, newest first, bounded by ``limit`` (used by the duplicate and follow-up checks)."""
    out, page = [], 1
    while len(out) < limit:
        res = search_complaints(open_only=True, page=page, page_size=config.PAGE_SIZE_MAX, db_path=db_path)
        out += res["rows"]
        if page >= res["pages"]:
            break
        page += 1
    return out[:limit]


def count_by(field: str, db_path: Optional[str] = None, **filters) -> dict[str, int]:
    """Counts grouped by status/category/priority/department, with the same filters as search_complaints."""
    f = c.normalize_filters(filters.get("status"), filters.get("category"), filters.get("priority"),
                            filters.get("search"), filters.get("created_from"), filters.get("created_to"),
                            filters.get("open_only", False), filters.get("owner_id"))
    return get_backend(db_path).count_by(field, f)


def complaints_df(db_path: Optional[str] = None, **filters) -> pd.DataFrame:
    return pd.DataFrame(list_complaints(db_path=db_path, **filters), columns=c.COMPLAINT_COLUMNS)


def get_stats(db_path: Optional[str] = None, **filters) -> dict:
    """total, submitted, under_review, in_progress, resolved, pending (= submitted + under_review)."""
    return c.stats_from_counts(count_by("status", db_path=db_path, **filters))


def set_image_path(complaint_id: str, image_path: str, db_path: Optional[str] = None) -> None:
    get_backend(db_path).set_image_path(complaint_id, image_path)


def delete_complaint(complaint_id: str, db_path: Optional[str] = None) -> bool:
    return get_backend(db_path).delete_complaint(complaint_id)


# ---- status ------------------------------------------------------------------------------------
def update_status(complaint_id: str, new_status: str, note: str = "", actor: str = "Reviewer (simulated)",
                  db_path: Optional[str] = None, expected_status: Optional[str] = None) -> bool:
    """Move a complaint ONE step forward (Submitted -> Under Review -> In Progress -> Resolved).

    Returns False if the complaint does not exist. Raises InvalidTransition for a disallowed change,
    ConflictError if ``expected_status`` no longer matches, DatabaseError if persistence fails.
    The status, its history entry and its activity event are written together.
    """
    return get_backend(db_path).update_status(complaint_id, new_status, note, actor, expected_status)


def get_status_history(complaint_id: str, db_path: Optional[str] = None) -> list[dict]:
    """Oldest first: {from_status, to_status, note, actor, changed_at}."""
    return get_backend(db_path).get_status_history(complaint_id)


# ---- activity ----------------------------------------------------------------------------------
def log_activity(complaint_id: Optional[str], agent: str, step: str, decision: str = "", reason: str = "",
                 outcome: str = "", db_path: Optional[str] = None, dedupe_key: Optional[str] = None) -> bool:
    """Record one event. Returns True if written, False if skipped because ``dedupe_key`` already exists.
    Storage failures RAISE (they are never swallowed)."""
    return get_backend(db_path).log_activity(complaint_id, {
        "agent": agent, "step": step, "decision": decision, "reason": reason, "outcome": outcome,
        "dedupe_key": dedupe_key})


def get_activity(complaint_id: Optional[str] = None, limit: int = 500, db_path: Optional[str] = None, *,
                 complaint_id_contains: Optional[str] = None, agent: Many = None, step: Many = None,
                 outcome_contains: Optional[str] = None, text: Optional[str] = None,
                 date_from: Union[date, str, None] = None, date_to: Union[date, str, None] = None,
                 offset: int = 0, newest_first: Optional[bool] = None) -> list[dict]:
    """Chronological for one complaint (a timeline); newest-first otherwise, unless ``newest_first`` is given."""
    f = c.normalize_activity_filters(complaint_id, complaint_id_contains, agent, step, outcome_contains, text,
                                     date_from, date_to)
    newest = (not complaint_id) if newest_first is None else newest_first
    return get_backend(db_path).get_activity(f, newest, max(1, min(int(limit), 5000)), max(0, int(offset)))


def count_activity(complaint_id: Optional[str] = None, db_path: Optional[str] = None, *,
                   complaint_id_contains: Optional[str] = None, agent: Many = None, step: Many = None,
                   outcome_contains: Optional[str] = None, text: Optional[str] = None,
                   date_from: Union[date, str, None] = None, date_to: Union[date, str, None] = None) -> int:
    f = c.normalize_activity_filters(complaint_id, complaint_id_contains, agent, step, outcome_contains, text,
                                     date_from, date_to)
    return get_backend(db_path).count_activity(f)


def distinct_activity_values(field: str, db_path: Optional[str] = None) -> list[str]:
    """Distinct 'agent' or 'step' values, for filter dropdowns."""
    return get_backend(db_path).distinct_activity(field)
