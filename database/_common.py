"""Backend-neutral rules shared by the SQLite and MongoDB repositories.

Keeping validation, filter normalisation and search semantics here guarantees both backends behave the
same way. Nothing in this module touches a database.

Search semantics (identical for every backend)
  * the query is split on whitespace into at most MAX_SEARCH_TOKENS tokens;
  * EVERY token must match (case-insensitive substring) in AT LEAST ONE of SEARCH_FIELDS;
  * ``%``, ``_`` and regex characters are literal text, never wildcards.
"""
import math
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import config
from models import utc_now

SEARCH_FIELDS = ("complaint_id", "description", "location", "summary", "department", "category")
MAX_SEARCH_TOKENS = 8
MAX_TOKEN_LEN = 100
SORTS = ("newest", "oldest", "updated", "priority")
COUNT_FIELDS = ("status", "category", "priority", "department")
PRIORITY_RANK = {p: i for i, p in enumerate(config.PRIORITIES)}   # Low=0 ... Critical=3
ACTIVITY_TEXT_FIELDS = ("step", "decision", "reason", "outcome", "agent")


class DatabaseError(RuntimeError):
    """Storage is unavailable or an operation failed. Messages never contain credentials."""


class InvalidTransition(ValueError):
    """The requested status change is not allowed."""


class ConflictError(RuntimeError):
    """The record changed since the caller last read it."""


def _as_list(value) -> Optional[list]:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, "")] or None
    return [value]


def _check_values(name: str, values: Optional[list], allowed: Iterable[str]) -> Optional[list]:
    if values is None:
        return None
    allowed = list(allowed)
    bad = [v for v in values if v not in allowed]
    if bad:
        raise ValueError(f"Invalid {name} filter: {bad[0]!r}")
    return values


def _day(value, name: str) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f"Invalid {name} date: {value!r}") from None


def search_tokens(search: Optional[str]) -> list[str]:
    return [t[:MAX_TOKEN_LEN] for t in (search or "").split()][:MAX_SEARCH_TOKENS]


def normalize_filters(status=None, category=None, priority=None, search=None,
                      created_from=None, created_to=None, open_only: bool = False,
                      owner_id: Optional[str] = None) -> dict:
    """Validate and canonicalise complaint filters. Raises ValueError on invalid values."""
    statuses = _check_values("status", _as_list(status), config.STATUSES)
    if open_only:
        statuses = [s for s in (statuses or config.OPEN_STATUSES) if s in config.OPEN_STATUSES]
        if not statuses:
            statuses = ["__none__"]            # explicit status filter excludes every open status
    d_from, d_to = _day(created_from, "start"), _day(created_to, "end")
    if d_from and d_to and d_from > d_to:
        raise ValueError("The start date is after the end date.")
    return {
        "statuses": statuses,
        "categories": _check_values("category", _as_list(category), config.CATEGORIES),
        "priorities": _check_values("priority", _as_list(priority), config.PRIORITIES),
        "tokens": search_tokens(search),
        # Legacy rows intentionally remain unowned; a citizen filter never includes them.
        "owner_id": str(owner_id).strip() if owner_id else None,
        # created_at is stored as ISO-8601 text, so ISO date prefixes compare correctly
        "created_from": d_from.isoformat() if d_from else None,
        "created_to_exclusive": (d_to + timedelta(days=1)).isoformat() if d_to else None,
    }


def normalize_activity_filters(complaint_id=None, complaint_id_contains=None, agent=None, step=None,
                               outcome_contains=None, text=None, date_from=None, date_to=None) -> dict:
    d_from, d_to = _day(date_from, "start"), _day(date_to, "end")
    if d_from and d_to and d_from > d_to:
        raise ValueError("The start date is after the end date.")
    return {
        "complaint_id": (complaint_id or "").strip() or None,
        "complaint_id_contains": (complaint_id_contains or "").strip()[:MAX_TOKEN_LEN] or None,
        "agents": _as_list(agent), "steps": _as_list(step),
        "outcome_tokens": search_tokens(outcome_contains), "text_tokens": search_tokens(text),
        "date_from": d_from.isoformat() if d_from else None,
        "date_to_exclusive": (d_to + timedelta(days=1)).isoformat() if d_to else None,
    }


def page_bounds(page: int, page_size: int) -> tuple[int, int]:
    """Return (page_size, offset) with sane clamping."""
    size = max(1, min(int(page_size or config.PAGE_SIZE_DEFAULT), config.PAGE_SIZE_MAX))
    return size, (max(1, int(page or 1)) - 1) * size


def page_result(rows: list, total: int, page: int, page_size: int) -> dict:
    size, _ = page_bounds(page, page_size)
    pages = max(1, math.ceil(total / size))
    return {"rows": rows, "total": total, "page": min(max(1, int(page or 1)), pages),
            "page_size": size, "pages": pages}


def new_complaint_id() -> str:
    """CF-YYYYMMDD-XXXX (uniqueness is enforced by the database; callers retry on collision)."""
    return f"CF-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(2).upper()}"


def build_complaint(record: dict, workflow_id: Optional[str] = None, is_demo: bool = False) -> dict:
    """Validate and normalise a new complaint. Returns a dict WITHOUT complaint_id (the backend assigns it)."""
    missing = [f for f in ("description", "category", "location", "priority", "department")
               if not str(record.get(f, "")).strip()]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")
    if record["category"] not in config.CATEGORIES:
        raise ValueError(f"Invalid category: {record['category']}")
    if record["priority"] not in config.PRIORITIES:
        raise ValueError(f"Invalid priority: {record['priority']}")
    status = record.get("status") or "Submitted"
    if status not in config.STATUSES:
        raise ValueError(f"Invalid status: {status}")
    if len(str(record["description"])) > config.DESCRIPTION_MAX * 2 or len(str(record["location"])) > config.LOCATION_MAX * 2:
        raise ValueError("Description or location is too long.")
    now = utc_now()
    created = record.get("created_at") or now
    return {
        "description": str(record["description"]).strip(), "category": record["category"],
        "location": str(record["location"]).strip(), "priority": record["priority"],
        "department": str(record["department"]).strip(), "status": status,
        "created_at": created, "updated_at": record.get("updated_at") or created,
        "summary": record.get("summary"), "draft": record.get("draft"),
        "priority_reason": record.get("priority_reason"), "image_path": record.get("image_path"),
        "workflow_id": workflow_id, "is_demo": bool(is_demo),
        "owner_id": str(record.get("owner_id") or "").strip() or None,
    }


def build_event(complaint_id: Optional[str], step: dict) -> dict:
    """Normalise a trace step (or ad-hoc event) into a stored activity event."""
    ev = {"complaint_id": complaint_id, "timestamp": step.get("timestamp") or utc_now(),
          "agent": str(step.get("agent") or "System"), "step": str(step.get("step") or ""),
          "decision": str(step.get("decision") or ""), "reason": str(step.get("reason") or ""),
          "outcome": str(step.get("outcome") or "")}
    if step.get("dedupe_key"):
        ev["dedupe_key"] = str(step["dedupe_key"])[:300]
    return ev


def check_transition(old: str, new: str) -> None:
    if new not in config.STATUSES:
        raise ValueError(f"Invalid status: {new}")
    if new == old:
        raise InvalidTransition(f"The status is already '{old}'.")
    if new not in config.STATUS_TRANSITIONS.get(old, []):
        allowed = config.STATUS_TRANSITIONS.get(old, [])
        hint = f"Allowed next status: {allowed[0]}." if allowed else "Resolved reports cannot change status."
        raise InvalidTransition(f"Cannot move from '{old}' to '{new}'. {hint}")


def clean_note(note: Optional[str]) -> str:
    n = " ".join((note or "").split())
    if len(n) > config.NOTE_MAX:
        raise ValueError(f"The note is too long (max {config.NOTE_MAX} characters).")
    return n


def stats_from_counts(by_status: dict) -> dict:
    stats = {"submitted": by_status.get("Submitted", 0), "under_review": by_status.get("Under Review", 0),
             "in_progress": by_status.get("In Progress", 0), "resolved": by_status.get("Resolved", 0)}
    stats["total"] = sum(stats.values())
    stats["pending"] = stats["submitted"] + stats["under_review"]       # Pending = Submitted + Under Review
    return stats


COMPLAINT_COLUMNS = ["complaint_id", "description", "category", "location", "priority", "department",
                     "status", "created_at", "updated_at", "summary", "draft", "priority_reason", "image_path"]
Row = dict[str, Any]
