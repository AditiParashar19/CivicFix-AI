"""Report an issue: a guided, human-in-the-loop workflow. NOTHING is saved until step 8 is confirmed."""
import pandas as pd
import streamlit as st

import config
import ui
import security
from agents.orchestrator import PartialSaveError, run_intake_workflow, save_report
from database import db
from services.validation import validate_image

principal = security.require_roles(set(security.ROLES))

DRAFT_STATE = ("workflow", "pending_image", "draft_edit", "dup_ack", "review_ack")


def clear_draft() -> None:
    for k in DRAFT_STATE:
        st.session_state.pop(k, None)


st.title("📝 Report a civic issue")
ui.simulation_note("<b>Review before use:</b> the agents suggest a category, priority and draft. "
                   "Nothing is saved until you confirm in the last step, and nothing is ever sent to a real department.")
ui.show_flash()

# ---- Steps 1-3: describe, optional extras, run the agents --------------------------------------------
ui.step_label(1, "Describe the issue")
with st.form("report_form"):
    description = st.text_area("What is the problem?", height=130, max_chars=config.DESCRIPTION_MAX,
                               placeholder="e.g. Large pothole near the City Market bus stop, bikes are swerving around it.")
    location = st.text_input("Where is it?", max_chars=config.LOCATION_MAX,
                             placeholder="Street, landmark or area (no personal addresses needed)")
    st.caption("**Step 2 · Optional details**")
    c1, c2 = st.columns(2)
    category_choice = c1.selectbox("Category (optional)", ["Auto-detect"] + [config.label(c) for c in config.CATEGORIES])
    image = c2.file_uploader("Photo (optional)", type=[e.strip(".") for e in config.ALLOWED_IMAGE_EXTENSIONS],
                             help=f"PNG, JPG or WEBP, up to {config.MAX_IMAGE_MB} MB.")
    analyze = st.form_submit_button("Step 3 · Run the analysis agents", type="primary")

# New feature: a lightweight report-quality coach. It does not make claims about the issue;
# it checks whether the citizen included enough useful context before running the agents.
if description.strip():
    desc_lower = description.lower()
    quality_checks = [
        ("Issue described", len(description.strip()) >= 25, "Add a little more detail about what is wrong."),
        ("Location included", bool(location.strip()), "Add a street, landmark, building or area."),
        ("Impact explained", any(w in desc_lower for w in ("danger", "unsafe", "blocked", "students", "traffic", "water", "smell", "accident", "people", "risk", "damage")), "Explain who or what is affected."),
        ("Requested action included", any(w in desc_lower for w in ("please", "repair", "remove", "clean", "inspect", "fix", "urgent", "replace", "check")), "Mention what action you want the authority to take."),
    ]
    with st.expander("✨ Complaint quality assistant", expanded=False):
        st.caption("Quick guidance to make your report more actionable. These are simple checks, not an AI verification of the facts.")
        for label, passed, advice in quality_checks:
            (st.success if passed else st.info)(f"{'✓' if passed else '○'} {label}" + ("" if passed else f" — {advice}"))
        st.progress(sum(1 for _, passed, _ in quality_checks) / len(quality_checks), text=f"Report completeness: {sum(1 for _, passed, _ in quality_checks)}/{len(quality_checks)} checks")

if analyze:
    clear_draft()                                   # a new analysis must never show or save the previous one
    hint = None if category_choice == "Auto-detect" else category_choice.lower()
    image_errors = validate_image(image.name, image.getvalue()) if image else []
    for e in image_errors:
        st.error(e)
    if not image_errors:
        try:
            with st.spinner("Agents are analysing your report…"):
                result = run_intake_workflow(description, location, hint)
        except Exception as exc:  # last-resort guard so the user never sees a traceback
            st.error(f"Something went wrong while analysing the report ({type(exc).__name__}). Nothing was saved.")
            result = None
        if result and not result["ok"]:
            for e in result["errors"]:
                st.error(e)
        elif result:
            st.session_state["workflow"] = result
            st.session_state["pending_image"] = (image.name, image.getvalue()) if image else None
            st.session_state["draft_edit"] = result["analysis"]["draft"]
            st.session_state["dup_ack"] = False
            st.session_state["review_ack"] = False

wf = st.session_state.get("workflow")
if not wf:
    st.stop()

a, d, p = wf["analysis"], wf["duplicates"], wf["plan"]
st.divider()
st.markdown('<div class="cf-lock">🔒 <b>Draft only: nothing has been saved yet.</b> Review steps 4 to 7, then confirm in step 8. '
            'Leaving this page or choosing Discard saves nothing.</div>', unsafe_allow_html=True)
with st.expander("The report these results are based on", expanded=False):
    st.text(f"Location: {a['location']}\n\n{a['description']}")
for w in wf.get("warnings", []):
    st.warning(w)

# ---- Step 4: classification and priority -----------------------------------------------------------------
ui.step_label(4, "Category and priority")
r1, r2 = st.columns(2)
with r1:
    st.markdown("**Category**")
    st.write(config.label(a["category"]))
    if a["category_source"] == "user":
        st.caption("Chosen by you.")
    else:
        st.caption(f"Detected by keyword rules · confidence **{a['confidence_label']}** ({a['confidence']}). "
                   f"{a['confidence_note']}")
        if a["confidence_label"] == "low":
            st.warning("The agent is unsure about the category. Consider choosing it yourself and re-running.")
with r2:
    st.markdown("**Priority**")
    st.markdown(ui.priority_badge(a["priority"]), unsafe_allow_html=True)
    st.caption(f"Rule score {a['priority_score']}. Every point has a stated reason:")
    for reason in a["priority_reason"].split("; "):
        st.write("• " + reason)
st.markdown("**Summary**")
st.text(a["summary"])

# ---- Step 5: duplicates -----------------------------------------------------------------------------------
ui.step_label(5, "Possible duplicates")
if d["method"] == "skipped":
    st.warning("The duplicate check could not run. Please search Complaint History yourself before saving.")
elif d["is_potential_duplicate"]:
    if principal.role == "citizen":
        st.warning("⚠️ The system found one or more similar open reports. Their details are hidden to protect other residents. "
                   "You may continue if this is a separate issue.")
    else:
        st.warning("⚠️ Similar open reports were found. A match is a warning, not a rejection: you can still submit "
                   "if yours is a separate issue.")
        ui.table(pd.DataFrame([{"ID": m["complaint_id"], "Match score": m["score"], "Status": m["status"],
                                "Category": config.label(m["category"]), "Location": m["location"],
                                "Why": ", ".join(m["reasons"])} for m in d["matches"]]))
    st.checkbox("I have reviewed these. My report is a separate issue.", key="dup_ack")
else:
    st.success(f"No similar open reports found (compared with {d.get('compared', 'the')} unresolved reports).")
st.caption(d.get("location_method", "Location similarity compares place names as text; no coordinates are used."))

# ---- Step 6: plan -----------------------------------------------------------------------------------------
ui.step_label(6, "Recommended department and next steps")
st.write(f"**{p['department']}** (mock directory) · simulated target response: **{p['sla_days']} day(s)**")
st.caption(p["reason"])
for step in p["actions"]:
    st.write("• " + step)

# ---- Step 7: draft ----------------------------------------------------------------------------------------
ui.step_label(7, "Edit the formal draft letter")
st.caption("Generated by the deterministic template." if a["text_source"] == "template" else
           "Polished by an optional AI provider (your text was sent to it). Validated for length and location.")
st.text_area("Draft", key="draft_edit", height=300, label_visibility="collapsed", max_chars=config.DRAFT_MAX)
with st.expander("Agent reasoning for this draft (not saved yet)"):
    ui.table(pd.DataFrame(wf["trace"])[["agent", "step", "decision", "reason", "outcome"]])

# ---- Step 8: confirm --------------------------------------------------------------------------------------
ui.step_label(8, "Confirm and save")
st.checkbox("I have reviewed the category, priority, duplicates and draft, and I want to save this report.",
            key="review_ack")
needs_dup = d["is_potential_duplicate"]
ready = bool(st.session_state.get("review_ack")) and (not needs_dup or bool(st.session_state.get("dup_ack")))
b1, b2 = st.columns(2)
if b1.button("Confirm and save report", type="primary", disabled=not ready):
    img = st.session_state.get("pending_image")
    try:
        cid = save_report(wf, draft=st.session_state.get("draft_edit"),
                          image_name=img[0] if img else None, image_bytes=img[1] if img else None,
                          duplicate_acknowledged=bool(st.session_state.get("dup_ack")),
                          owner_id=principal.subject)
    except PartialSaveError as exc:
        clear_draft()
        ui.flash(f"{exc} You can add the photo later from your own files.", "warning")
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except db.DatabaseError as exc:
        st.error(f"⚠️ NOT saved: {exc} Your draft is still here: fix the storage problem and press Confirm again.")
    else:
        clear_draft()
        ui.flash(f"Report saved as **{cid}**. It is stored in this prototype only (simulated routing: no department "
                 "was contacted). Find it under Complaint History.")
        st.rerun()
if b2.button("Discard draft"):
    clear_draft()
    st.rerun()
if not ready:
    st.caption("Tick the confirmation" + (" and the duplicate acknowledgement" if needs_dup else "") + " to enable saving.")
