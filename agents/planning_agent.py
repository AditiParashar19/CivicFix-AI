"""Planning / department-recommendation agent (uses the MOCK directory).

plan_resolution(analysis, duplicates) -> PlanResult
Produces a recommended route and next steps. It never contacts anyone: requires_human_review and
simulated are always True.
"""
from models import trace_step
from services import llm
from services.department_directory import get_department

AGENT = "Planning Agent"


def validate_plan(plan: dict) -> None:
    if not plan.get("department") or not plan.get("actions"):
        raise ValueError("plan needs a department and at least one action")
    if plan.get("requires_human_review") is not True or plan.get("simulated") is not True:
        raise ValueError("plans must require human review and be marked simulated")
    if not isinstance(plan.get("sla_days"), int) or plan["sla_days"] < 1:
        raise ValueError("sla_days must be a positive integer")
    if not str(plan.get("contact_email", "")).endswith(".example"):
        raise ValueError("contact addresses must come from the mock directory (.example)")


def llm_tips(analysis: dict) -> list[str]:
    """Up to two short, practical tips for the citizen (safety, evidence to attach). Never raises; [] offline."""
    if not llm.available():
        return []
    prompt = ("Give at most 2 short practical tips (each under 140 characters) for a citizen reporting this civic "
              "issue: a safety precaution if relevant and useful evidence to attach. No promises about what the "
              "authority will do.\n"
              f"Category: {analysis['category']}  Priority: {analysis['priority']}\n"
              f"Location: {llm.wrap(analysis['location'])}\nDetails: {llm.wrap(analysis['description'][:600])}\n"
              'Return JSON: {"tips": ["...", "..."]}')
    obj, _ = llm.complete_json(prompt, max_tokens=250, timeout=12)
    tips = obj.get("tips") if obj else None
    if not isinstance(tips, list):
        return []
    return [" ".join(str(t).split())[:160] for t in tips if str(t).strip()][:2]


def plan_resolution(analysis: dict, duplicates: dict | None = None, use_llm: bool = True,
                    extra_tips: list[str] | None = None) -> dict:
    dept = get_department(analysis["category"])
    priority = analysis["priority"]
    sla = dept["sla_days"]
    if priority == "Critical":
        sla = 1
    elif priority == "High":
        sla = max(1, sla // 2)

    actions = [
        "Review and edit the AI-drafted complaint (human review is required).",
        f"Route to: {dept['name']} (mock contact {dept['email']}). CivicFix does not send anything; "
        "submit through the official channel yourself.",
        f"Target response time (mock): {sla} day(s). The Follow-up Agent will flag this report if it stalls.",
    ]
    tips = extra_tips if extra_tips is not None else (llm_tips(analysis) if use_llm else [])
    if priority in ("High", "Critical"):
        actions.insert(2, f"Mark as urgent ({priority}): ask for an expedited inspection.")
    advice = None
    if duplicates and duplicates.get("is_potential_duplicate"):
        top = duplicates["matches"][0]
        advice = (f"A similar open report exists ({top['complaint_id']}, status {top['status']}). "
                  "Consider supporting that report instead; submit a new one only if this is a different issue.")
        actions.append(advice)
    actions += [f"AI tip: {t}" for t in tips]

    base = dept["sla_days"]
    sla_basis = (f"{priority} priority overrides the department default of {base} day(s) with 1 day" if priority == "Critical"
                 else f"{priority} priority halves the department default of {base} day(s)" if priority == "High"
                 else f"department default of {base} day(s)")
    reason = (f"Category '{analysis['category']}' maps to {dept['name']} in the mock directory. "
              f"Simulated target response: {sla} day(s) ({sla_basis}). No department has received this report.")
    plan = {"department": dept["name"], "contact_email": dept["email"], "sla_days": sla,
            "escalation_contact": dept["escalation"], "actions": actions, "duplicate_advice": advice,
            "requires_human_review": True, "simulated": True, "sla_basis": sla_basis, "reason": reason}
    plan["trace"] = [trace_step(AGENT, "recommend_department", f"Department = {dept['name']}", reason,
                                "Recommendation only (simulated): no department was contacted.")]
    validate_plan(plan)
    return plan