"""Complaint history: search/filter/paginate, full details, status timeline, agent decisions, simulated status updates."""
import pandas as pd
import streamlit as st

import config
import ui
import security
from database import db

principal = security.require_roles(set(security.ROLES))

st.title("📁 Complaint history")
ui.show_flash()

f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
search = f1.text_input("Search", key="hist_q", placeholder="ID, location or keywords (all words must match)")
status_f = f2.selectbox("Status", ["All"] + config.STATUSES, key="hist_status")
cat_f = f3.selectbox("Category", ["All"] + config.CATEGORIES, key="hist_cat", format_func=lambda v: v if v == "All" else config.label(v))
pri_f = f4.selectbox("Priority", ["All"] + config.PRIORITIES, key="hist_pri")
filters = dict(status=None if status_f == "All" else status_f, category=None if cat_f == "All" else cat_f,
               priority=None if pri_f == "All" else pri_f, search=search.strip() or None,
               owner_id=None if principal.is_staff else principal.subject)

page = ui.current_page("hist", repr(sorted((k, str(v)) for k, v in filters.items())))
with st.spinner("Loading…"):
    res = ui.guarded(db.search_complaints, page=page, page_size=10, **filters)
if res is None:
    st.stop()
if res["total"] == 0:
    st.info("No complaints match. Clear the search or filters, or report a new issue.")
    st.stop()

ui.table(pd.DataFrame([{"ID": r["complaint_id"], "Category": config.label(r["category"]), "Location": r["location"],
                        "Priority": f"{ui.PRIORITY_ICON[r['priority']]} {r['priority']}", "Status": r["status"],
                        "Created": ui.fmt_ts(r["created_at"]), "Last update": ui.fmt_ts(r["updated_at"])}
                       for r in res["rows"]]))
ui.page_controls("hist", res["page"], res["pages"], res["total"])

st.subheader("Complaint details")
rows = res["rows"]
ids = [r["complaint_id"] for r in rows]
if st.session_state.get("hist_selected") not in ids:     # keep the selection valid for the CURRENT results
    st.session_state["hist_selected"] = ids[0]
by_id = {r["complaint_id"]: r for r in rows}
chosen = st.selectbox("Select a complaint from this page", ids, key="hist_selected",
                      format_func=lambda i: f"{i} · {by_id[i]['location']}")
c = ui.guarded(db.get_complaint, chosen, owner_id=None if principal.is_staff else principal.subject)
if c is None:
    st.warning("That complaint could not be loaded (it may have been removed). Refresh the list.")
    st.stop()

st.markdown(f"### {c['complaint_id']}  {ui.status_badge(c['status'])} {ui.priority_badge(c['priority'])}",
            unsafe_allow_html=True)    # ID, status and priority come from validated constants/format only
d1, d2 = st.columns(2)
d1.text(f"Category:  {config.label(c['category'])}\nLocation:  {c['location']}")
d2.text(f"Department (mock): {c['department']}\nCreated: {ui.fmt_ts(c['created_at'])}  ·  Updated: {ui.fmt_ts(c['updated_at'])}")
if c.get("is_demo"):
    st.caption("🧪 Fictional demo record.")
st.markdown("**Summary**")
st.text(c["summary"] or "-")
st.markdown("**Original description**")
st.text(c["description"])

# New feature: let residents/admins export a shareable plain-text case summary.
report_text = (
    f"CivicFix AI — Civic Complaint Summary\n"
    f"Complaint ID: {c['complaint_id']}\n"
    f"Status: {c['status']}\n"
    f"Priority: {c['priority']}\n"
    f"Category: {config.label(c['category'])}\n"
    f"Suggested department (prototype): {c['department']}\n"
    f"Location: {c['location']}\n"
    f"Created: {ui.fmt_ts(c['created_at'])}\n"
    f"Last updated: {ui.fmt_ts(c['updated_at'])}\n\n"
    f"Summary\n{c.get('summary') or '-'}\n\n"
    f"Original description\n{c['description']}\n\n"
    f"Priority reasoning\n{c.get('priority_reason') or '-'}\n\n"
    f"Draft letter (not sent)\n{c.get('draft') or '-'}\n\n"
    "Note: CivicFix AI is a prototype. Department routing and status updates may be simulated; this export is not proof of official filing.\n"
)
st.download_button("⬇️ Download complaint summary (.txt)", data=report_text,
                   file_name=f"civicfix_{c['complaint_id']}.txt", mime="text/plain")

tab_why, tab_draft, tab_status, tab_agents = st.tabs(["Why this priority", "Drafted letter", "Status & history", "Agent decisions"])
with tab_why:
    if c.get("priority_reason"):
        for reason in c["priority_reason"].split("; "):
            st.write("• " + reason)
    else:
        st.info("No priority explanation was stored for this report.")
with tab_draft:
    if c.get("draft"):
        st.text(c["draft"])
        st.caption("A draft only. It was not sent to any department.")
    else:
        st.info("No draft stored.")
    if c.get("image_path") and (config.ROOT / c["image_path"]).is_file():
        st.image(str(config.ROOT / c["image_path"]), caption="Attached photo", width=360)

with tab_status:
    history = ui.guarded(db.get_status_history, c["complaint_id"], default=[])
    st.markdown("**Status timeline** (simulated prototype statuses)")
    if history:
        ui.table(pd.DataFrame([{"When": ui.fmt_ts(h["changed_at"]), "From": h["from_status"] or "(created)",
                                "To": h["to_status"], "Note": h["note"] or "", "By": h["actor"] or ""} for h in history]))
    else:
        st.info("No status history recorded.")

    allowed = config.STATUS_TRANSITIONS.get(c["status"], [])
    if not principal.is_staff:
        st.info("Status changes are handled by authorized municipal reviewers and operators.")
    elif not allowed:
        st.info("This report is Resolved (simulated). Resolved reports cannot change status.")
    elif not security.role_can_change_status(principal.role, c["status"], allowed[0]):
        st.info("Your role cannot make the next status transition.")
    else:
        st.markdown("**Update status** (simulated)")
        ui.simulation_note("Changing a status here <b>simulates</b> a department update. It does not contact anyone and "
                           "is not evidence that a real authority did any work.")
        allowed = config.STATUS_TRANSITIONS.get(c["status"], [])
        nxt = allowed[0]
        with st.form(f"status_form_{c['complaint_id']}"):
            st.write(f"Current status: **{c['status']}** → next allowed status: **{nxt}**")
            note = st.text_input("Note (optional)", max_chars=config.NOTE_MAX)
            confirm = st.checkbox("I confirm this simulated status change.")
            go = st.form_submit_button(f"Move to {nxt}")
        if go:
            if not confirm:
                st.warning("Tick the confirmation box to apply the change.")
            else:
                try:
                    ok = db.update_status(c["complaint_id"], nxt, note, actor=f"{principal.role.title()} (simulated)",
                                          expected_status=c["status"])
                except (db.InvalidTransition, db.ConflictError, ValueError) as exc:
                    st.error(str(exc))
                except db.DatabaseError as exc:
                    st.error(f"⚠️ The status was NOT changed: {exc}")
                else:
                    if ok:
                        ui.flash(f"{c['complaint_id']} moved to {nxt} (simulated).")
                        st.rerun()
                    else:
                        st.error("The complaint no longer exists; nothing was changed.")

with tab_agents:
    events = ui.guarded(db.get_activity, c["complaint_id"], 500, default=[])
    if not events:
        st.info("No agent activity recorded for this complaint.")
    for e in events:
        with st.expander(f"{ui.fmt_ts(e['timestamp'])} · {e['agent']} · {e['step']}"):
            st.text(f"Decision: {e['decision']}\nReason:   {e['reason']}\nOutcome:  {e['outcome']}")
