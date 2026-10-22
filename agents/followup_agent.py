"""Follow-up / escalation agent.

evaluate_followups(complaints, threshold_days, now=None) -> list[FollowUpDecision]
Rules (stale_days = days since last update):
  * resolved reports are ignored
  * stale_days < threshold                          -> none     (not yet due)
  * stale_days >= 2 x threshold                     -> escalate
  * priority High/Critical and stale_days >= threshold -> escalate
  * otherwise stale_days >= threshold               -> remind
Reminders and escalations are only DRAFTED and logged (simulated). Nothing is ever sent.
"""
from datetime import datetime, timezone
from typing import Optional

import config
from models import trace_step
from services import llm
from services.department_directory import get_department

AGENT = "Follow-up Agent"
_ORDER = {"escalate": 0, "remind": 1, "none": 2}


_LABEL = {"none": "No action recommended", "remind": "Recommended reminder", "escalate": "Recommended escalation"}
STATUS_NOTE = ("Persisted status of this prototype record. Statuses are simulated and do not show that a real "
               "authority has acted.")


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def evaluate_followups(complaints: list[dict], threshold_days: float = config.FOLLOWUP_THRESHOLD_DAYS,
                       now: Optional[datetime] = None) -> list[dict]:
    """Recommend (never perform) reminders/escalations. Resolved reports are skipped.

    Each decision keeps ``action`` ("none"/"remind"/"escalate") plus a human label (``recommendation``) and the
    ``persisted_status`` read from storage; the agent never changes a status or sends anything.
    """
    if not isinstance(threshold_days, (int, float)) or threshold_days <= 0:
        raise ValueError("threshold_days must be a positive number")
    now = now or datetime.now(timezone.utc)
    decisions = []
    for c in complaints:
        status = c.get("status")
        if status == "Resolved":
            continue
        base = {"complaint_id": c.get("complaint_id", "?"), "status": status, "persisted_status": status,
                "priority": c.get("priority"), "department": c.get("department"), "category": c.get("category", "other"),
                "basis_updated_at": c.get("updated_at") or c.get("created_at"), "simulated": True,
                "status_note": STATUS_NOTE}
        try:
            created = _parse(c["created_at"])
            updated = _parse(c.get("updated_at") or c["created_at"])
        except (KeyError, TypeError, ValueError):
            decisions.append({**base, "age_days": 0.0, "stale_days": 0.0, "action": "none",
                              "recommendation": _LABEL["none"], "error": True,
                              "reason": "Timestamps are missing or invalid, so this report could not be evaluated."})
            continue
        age = max(0.0, (now - created).total_seconds() / 86400)       # clock skew never yields negative ages
        stale = max(0.0, (now - updated).total_seconds() / 86400)
        urgent = c.get("priority") in ("High", "Critical")
        if stale < threshold_days:
            action, reason = "none", f"Updated {stale:.1f} day(s) ago, under the {threshold_days}-day threshold."
        elif stale >= 2 * threshold_days:
            action, reason = "escalate", (f"No update for {stale:.1f} days, at least twice the "
                                          f"{threshold_days}-day threshold.")
        elif urgent:
            action, reason = "escalate", (f"{c['priority']} priority report with no update for {stale:.1f} days "
                                          f"(threshold {threshold_days}).")
        else:
            action, reason = "remind", f"No update for {stale:.1f} days (threshold {threshold_days})."
        decisions.append({**base, "age_days": round(age, 1), "stale_days": round(stale, 1), "action": action,
                          "recommendation": _LABEL[action], "reason": reason, "error": False})
    decisions.sort(key=lambda d: (_ORDER[d["action"]], -d["stale_days"]))
    return decisions


def _llm_body(decision: dict, kind: str, fallback: str) -> str:
    """Optional LLM wording of the reminder/escalation. Must keep the ID and stay short, else the template wins."""
    if not llm.available():
        return fallback
    prompt = (f"Write a polite, firm {kind} message (max 70 words, plain text, no greeting or signature) to a "
              "municipal department. Use ONLY these facts and promise nothing.\n"
              f"Complaint ID: {decision['complaint_id']}; status: {decision['status']}; priority: "
              f"{decision['priority']}; days without update: {decision['stale_days']}; "
              f"category: {decision.get('category', 'other')}.")
    text, _ = llm.complete(prompt, max_tokens=200, timeout=12)
    if text and decision["complaint_id"] in text and 20 <= len(text) <= 700:
        return text.strip()
    return fallback


def draft_message(decision: dict, use_llm: bool = True) -> str:
    dept = get_department(decision.get("category", "other"))
    if decision["action"] == "escalate":
        to = dept["escalation"]
        body = (f"Escalation request: complaint {decision['complaint_id']} ({decision['priority']} priority) "
                f"has had no update for {decision['stale_days']} days. Please review urgently.")
        kind = "escalation"
    else:
        to = dept["name"]
        body = (f"Reminder: complaint {decision['complaint_id']} is still '{decision['status']}' with no update "
                f"for {decision['stale_days']} days. Please share a status update.")
        kind = "reminder"
    if use_llm:
        body = _llm_body(decision, kind, body)
    return f"To: {to} (mock directory)\n{body}\n\n[SIMULATED. This message was NOT sent.]"


def record_followups(decisions: list[dict], db_path=None) -> int:
    """Write each due recommendation to the activity log as a simulated action.

    Idempotent: the same recommendation for the same last-update time is logged once, so Streamlit reruns or
    repeated clicks cannot create duplicate events. Returns the number of NEW events written; storage
    errors propagate to the caller.
    """
    from database import db
    n = 0
    for d in decisions:
        if d["action"] == "none" or d.get("error"):
            continue
        step = trace_step(AGENT, "reminder_drafted" if d["action"] == "remind" else "escalation_drafted",
                          f"Recommend {d['action']}", d["reason"],
                          "Simulated: message drafted and logged, nothing was sent.")
        key = f"followup:{d['action']}:{d['complaint_id']}:{d.get('basis_updated_at')}"
        if db.log_activity(d["complaint_id"], step["agent"], step["step"], step["decision"], step["reason"],
                           step["outcome"], db_path=db_path, dedupe_key=key):
            n += 1
    return n