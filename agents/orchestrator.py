"""Orchestrates the intake workflow: validate -> Intake Agent -> Duplicate Agent -> Planning Agent.

Each stage receives structured output from the previous one, is validated, and falls back to a safe
default (recorded in the trace AND the persistent activity log) instead of crashing. NOTHING about a new
report is persisted until save_report(), which a human triggers after reviewing the results.
"""
import uuid
from typing import Optional

import config
from agents.complaint_agent import analyze_complaint, validate_analysis
from agents.duplicate_agent import find_duplicates
from agents.planning_agent import llm_tips, plan_resolution, validate_plan
from database import db
from models import trace_step
from services import llm
from services.validation import normalize_text, save_image, validate_image, validate_report_input

AGENT = "Orchestrator"


class PartialSaveError(RuntimeError):
    """The complaint WAS saved, but a follow-up step (the photo) failed. Carries the complaint ID."""

    def __init__(self, complaint_id: str, message: str):
        super().__init__(message)
        self.complaint_id = complaint_id


def _record_failure(step: dict, warnings: list, db_path) -> None:
    """Persist a stage failure as an unattached event. A logging failure is surfaced, not swallowed."""
    try:
        db.log_activity(None, step["agent"], step["step"], step["decision"], step["reason"], step["outcome"],
                        db_path=db_path)
    except Exception as exc:
        warnings.append(f"A workflow failure could not be written to the activity log ({type(exc).__name__}).")


def _stage(name, run, validate, fallback, trace, warnings, db_path):
    try:
        out = run()
        validate(out)
        return out
    except Exception as exc:  # bounded autonomy: record the failure, use a safe default
        step = trace_step(AGENT, f"{name}_failed", "Using safe fallback", f"{type(exc).__name__}: {exc}",
                          "Workflow continues; a human reviews the result.")
        trace.append(step)
        _record_failure(step, warnings, db_path)
        out = fallback()
        validate(out)       # if even the fallback is invalid the caller reports the workflow as failed
        return out


def _validate_duplicates(result: dict) -> None:
    if not isinstance(result.get("matches"), list) or "is_potential_duplicate" not in result:
        raise ValueError("duplicate result must contain 'matches' (list) and 'is_potential_duplicate'")
    for m in result["matches"]:
        if not m.get("complaint_id"):
            raise ValueError("every duplicate match needs a complaint_id")


def run_intake_workflow(description: str, location: str, category_hint: Optional[str] = None,
                        use_llm: bool = True, db_path: Optional[str] = None) -> dict:
    """Analyse a DRAFT report. Reads storage (duplicate check) but never writes a complaint."""
    description, location = normalize_text(description, multiline=True), normalize_text(location)
    errors = validate_report_input(description, location)
    if errors:
        return {"ok": False, "errors": errors, "warnings": [], "analysis": None, "duplicates": None,
                "plan": None, "trace": [], "workflow_id": None}

    warnings: list[str] = []
    trace = [trace_step(AGENT, "start", "Run intake workflow", "New report received (draft, not saved)",
                        "Stages: Intake -> Duplicate check -> Planning (all validated).")]
    try:
        analysis = _stage("intake",
                          lambda: analyze_complaint(description, location, category_hint, use_llm),
                          validate_analysis,
                          lambda: analyze_complaint(description, location, None, use_llm=False),
                          trace, warnings, db_path)
        trace += analysis["trace"]
        # Performance: the planning tips need only the analysis, so the LLM call overlaps with the duplicate check.
        tips_future = llm.submit(llm_tips, analysis) if (use_llm and llm.available()) else None

        def _no_dupes():
            return {"is_potential_duplicate": False, "threshold": config.DUPLICATE_THRESHOLD, "method": "skipped",
                    "matches": [], "trace": []}
        duplicates = _stage("duplicate_check",
                            lambda: find_duplicates(description, location, analysis["category"], db_path=db_path),
                            _validate_duplicates, _no_dupes, trace, warnings, db_path)
        if duplicates["method"] == "skipped":
            warnings.append("The duplicate check could not run. Search Complaint History manually before saving.")
        trace += duplicates["trace"]

        try:
            tips = tips_future.result(timeout=20) if tips_future else None
        except Exception:
            tips = []
        plan = _stage("planning", lambda: plan_resolution(analysis, duplicates, use_llm=use_llm, extra_tips=tips),
                      validate_plan,
                      lambda: plan_resolution({**analysis, "category": "other"}, None, use_llm=False), trace, warnings, db_path)
        trace += plan["trace"]
    except Exception as exc:       # even the safe fallback failed: report it, do not pretend success
        return {"ok": False, "errors": [f"The agents could not analyse this report ({type(exc).__name__}). "
                                        "Please try again."], "warnings": warnings, "analysis": None,
                "duplicates": None, "plan": None, "trace": trace, "workflow_id": None}

    trace.append(trace_step(AGENT, "await_human_review", "Stop and wait for the citizen",
                            "Autonomy is bounded: saving requires explicit confirmation.",
                            "Draft only. NOTHING has been saved; awaiting review of the draft and duplicate warning."))
    return {"ok": True, "errors": [], "warnings": warnings, "analysis": analysis, "duplicates": duplicates,
            "plan": plan, "trace": trace, "workflow_id": uuid.uuid4().hex,
            "input": {"description": description, "location": location, "category_hint": category_hint}}


def save_report(workflow: dict, draft: Optional[str] = None, image_name: Optional[str] = None,
                image_bytes: Optional[bytes] = None, duplicate_acknowledged: bool = False,
                db_path: Optional[str] = None, owner_id: Optional[str] = None) -> str:
    """Persist a reviewed workflow result and return the complaint ID.

    This is the ONLY point at which a new complaint is stored; it never contacts any department.
    Idempotent per workflow: saving the same reviewed workflow twice (e.g. a double click or a Streamlit
    rerun) returns the original ID instead of creating a second complaint.
    Raises ValueError for rule violations, DatabaseError if storage fails, PartialSaveError if only the photo failed.
    """
    if not workflow or not workflow.get("ok"):
        raise ValueError("Cannot save a workflow that did not pass validation.")
    if config.IS_PRODUCTION and not str(owner_id or "").strip():
        raise ValueError("A verified account is required to store a production complaint.")
    a, p, d = workflow["analysis"], workflow["plan"], workflow["duplicates"]
    validate_analysis(a)
    validate_plan(p)
    wid = workflow.get("workflow_id")
    if not wid:
        raise ValueError("The workflow has no ID; please analyse the report again.")

    existing = db.get_complaint_id_by_workflow(wid, db_path=db_path)
    if existing:                      # already saved: do not duplicate, do not re-log
        return existing

    if d["is_potential_duplicate"] and not duplicate_acknowledged:
        raise ValueError("A possible duplicate was found. Please confirm this is a separate issue to continue.")
    if image_bytes:
        img_errors = validate_image(image_name or "", image_bytes)
        if img_errors:
            raise ValueError(" ".join(img_errors))

    final_draft = normalize_text(draft, multiline=True) if draft is not None else a["draft"]
    final_draft = final_draft or a["draft"]
    if len(final_draft) > config.DRAFT_MAX:
        raise ValueError(f"The draft is too long (max {config.DRAFT_MAX} characters).")

    # Re-check at save time: a similar report may have been filed since the analysis was run.
    if not d["is_potential_duplicate"] and d.get("method") != "skipped":
        fresh = find_duplicates(a["description"], a["location"], a["category"], db_path=db_path)
        if fresh["is_potential_duplicate"] and not duplicate_acknowledged:
            raise ValueError(f"A similar report ({fresh['matches'][0]['complaint_id']}) was filed after your analysis. "
                             "Please run the analysis again and review it.")

    backend = db.describe_backend(db_path)
    trace = list(workflow["trace"])
    if final_draft != a["draft"]:
        trace.append(trace_step("Human Review", "edit_draft", "Draft edited by the citizen",
                                "The citizen reviewed and changed the generated draft.", "Edited draft stored."))
    if d["is_potential_duplicate"]:
        trace.append(trace_step("Human Review", "duplicate_override", "Submit despite possible duplicate",
                                f"Citizen confirmed a separate issue (closest: {d['matches'][0]['complaint_id']}).",
                                "Report accepted as new."))
    trace.append(trace_step("Human Review", "confirm_save", f"Saved to the prototype database ({backend['label']})",
                            "Citizen reviewed the draft and confirmed.",
                            "SIMULATED routing: no department was contacted and no real complaint was filed."))
    record = {"description": a["description"], "category": a["category"], "location": a["location"],
              "priority": a["priority"], "department": p["department"], "summary": a["summary"],
              "draft": final_draft, "priority_reason": a["priority_reason"], "owner_id": owner_id}
    cid = db.insert_complaint(record, trace=trace, db_path=db_path, workflow_id=wid)
    if image_bytes:
        try:
            db.set_image_path(cid, save_image(cid, image_name, image_bytes), db_path=db_path)
        except Exception as exc:
            try:
                db.log_activity(cid, "System", "image_save_failed", "Photo not attached",
                                f"{type(exc).__name__}", "The report was saved without its photo.", db_path=db_path)
            except Exception:
                pass
            raise PartialSaveError(cid, f"Report {cid} was saved, but its photo could not be stored "
                                        f"({type(exc).__name__}).") from exc
    return cid