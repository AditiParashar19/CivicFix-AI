"""Shared data contracts. Every member codes against these shapes.

All agents return plain dicts (JSON-serialisable) shaped like the TypedDicts below,
and every agent result carries a ``trace`` list of TraceStep dicts for the activity log.
"""
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict


def utc_now() -> str:
    """ISO-8601 UTC timestamp, second precision. Used for every stored timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TraceStep(TypedDict):
    timestamp: str
    agent: str      # e.g. "Intake Agent"
    step: str       # e.g. "classify"
    decision: str   # what was decided
    reason: str     # why
    outcome: str    # result / effect


def trace_step(agent: str, step: str, decision: str, reason: str, outcome: str) -> TraceStep:
    return TraceStep(timestamp=utc_now(), agent=agent, step=step,
                     decision=decision, reason=reason, outcome=outcome)


class ClassificationResult(TypedDict):          # services.classifier.classify
    category: str                               # one of config.CATEGORIES
    confidence: float                           # 0..1
    scores: dict[str, int]
    matched_keywords: list[str]


class PriorityResult(TypedDict):                # services.classifier.assess_priority
    priority: str                               # one of config.PRIORITIES
    score: int
    reasons: list[str]


class ComplaintAnalysis(TypedDict):             # agents.complaint_agent.analyze_complaint
    description: str
    location: str
    category: str
    category_source: str                        # "user" | "classifier"
    confidence: float
    priority: str
    priority_score: int
    priority_reason: str
    summary: str
    draft: str
    text_source: str                            # "template" | "llm"
    confidence_label: str                       # "low" | "medium" | "high" | "set by user"
    confidence_note: str
    trace: list[TraceStep]


class DuplicateMatch(TypedDict):
    complaint_id: str
    score: float
    text_similarity: float
    location_similarity: float
    same_category: bool
    status: str
    category: str
    location: str
    summary: str
    reasons: list[str]


class DuplicateResult(TypedDict):               # agents.duplicate_agent.find_duplicates
    is_potential_duplicate: bool
    threshold: float
    method: str                                 # "tfidf" | "token-overlap" | "skipped"
    compared: int
    matches: list[DuplicateMatch]
    trace: list[TraceStep]


class PlanResult(TypedDict):                    # agents.planning_agent.plan_resolution
    department: str
    contact_email: str                          # MOCK address
    sla_days: int                               # MOCK service-level target
    escalation_contact: str
    actions: list[str]
    duplicate_advice: Optional[str]
    requires_human_review: bool                 # always True
    simulated: bool                             # always True
    sla_basis: str
    reason: str
    trace: list[TraceStep]


class FollowUpDecision(TypedDict):              # agents.followup_agent.evaluate_followups
    complaint_id: str
    status: str
    priority: str
    department: str
    age_days: float
    stale_days: float
    action: str                                 # "none" | "remind" | "escalate"
    reason: str
    simulated: bool


class WorkflowResult(TypedDict):                # agents.orchestrator.run_intake_workflow
    ok: bool
    errors: list[str]
    warnings: list[str]                         # non-fatal problems (e.g. duplicate check skipped)
    workflow_id: Optional[str]                  # makes save_report idempotent
    analysis: Optional[ComplaintAnalysis]
    duplicates: Optional[DuplicateResult]
    plan: Optional[PlanResult]
    trace: list[TraceStep]


Record = dict[str, Any]   # a row of the complaints table (see database/db.py SCHEMA)
