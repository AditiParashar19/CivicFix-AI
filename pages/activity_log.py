"""Agent activity explorer: every step, decision, reason and outcome, with filters and pagination."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import ui
import security
from database import db

security.require_roles({"reviewer", "operator", "admin"})

st.title("🧾 Agent activity log")
st.caption("Each event is one agent or system decision with its reasoning and outcome. Events with a complaint ID "
           "belong to a saved report. Events without one are system events (for example a failed agent stage). "
           "Drafts that were never saved are not stored.")
ui.show_flash()

agents = ui.guarded(db.distinct_activity_values, "agent", default=[])
steps = ui.guarded(db.distinct_activity_values, "step", default=[])

with st.expander("Filters", expanded=True):
    f1, f2, f3 = st.columns(3)
    sel_agents = f1.multiselect("Agent", agents, key="act_agents")
    sel_steps = f2.multiselect("Event type (step)", steps, key="act_steps")
    cid = f3.text_input("Complaint ID contains", key="act_cid")
    g1, g2, g3 = st.columns(3)
    outcome = g1.text_input("Outcome contains", key="act_outcome")
    text = g2.text_input("Search decision / reason", key="act_text")
    order = g3.radio("Order", ["Newest first", "Oldest first"], key="act_order", horizontal=True)
    use_dates = st.checkbox("Filter by date", key="act_use_dates")
    d_from = d_to = None
    if use_dates:
        h1, h2 = st.columns(2)
        d_from = h1.date_input("From", value=date.today() - timedelta(days=7), key="act_from")
        d_to = h2.date_input("To", value=date.today(), key="act_to")
    size = st.selectbox("Events per page", [10, 25, 50], index=1, key="act_size")

filters = dict(agent=sel_agents or None, step=sel_steps or None, complaint_id_contains=cid.strip() or None,
               outcome_contains=outcome.strip() or None, text=text.strip() or None, date_from=d_from, date_to=d_to)
newest = order == "Newest first"
if st.button("🔄 Refresh"):
    st.rerun()

page = ui.current_page("act", (repr(sorted((k, str(v)) for k, v in filters.items())), newest, size))
with st.spinner("Loading events…"):
    total = ui.guarded(db.count_activity, **filters)
    if total is None:
        st.stop()
    page = min(page, max(1, -(-total // size)))
    events = ui.guarded(db.get_activity, limit=size, offset=(page - 1) * size, newest_first=newest, **filters)
if events is None:
    st.stop()
if total == 0:
    st.info("No events match these filters. No agent activity yet? Submit a report to see the agents at work.")
    st.stop()

ui.table(pd.DataFrame([{"Time (UTC)": ui.fmt_ts(e["timestamp"]), "Complaint": e["complaint_id"] or "(system)",
                        "Agent": e["agent"], "Step": e["step"], "Decision": e["decision"], "Outcome": e["outcome"]}
                       for e in events]))
ui.page_controls("act", page, max(1, -(-total // size)), total)

st.subheader("Event details")
for e in events:
    with st.expander(f"{ui.fmt_ts(e['timestamp'])} · {e['agent']} · {e['step']}"):
        st.text(f"Complaint: {e['complaint_id'] or '(system event)'}\nDecision:  {e['decision']}\n"
                f"Reason:    {e['reason']}\nOutcome:   {e['outcome']}")
