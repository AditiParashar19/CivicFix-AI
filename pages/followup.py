"""Follow-up agent: find stalled reports and RECOMMEND reminders or escalation (nothing is ever sent)."""
import pandas as pd
import streamlit as st

import config
import ui
import security
from agents.followup_agent import STATUS_NOTE, draft_message, evaluate_followups, record_followups
from database import db

principal = security.require_roles({"operator", "admin"})

st.title("⏰ Follow-up agent")
ui.simulation_note("The agent only <b>recommends</b> and drafts reminders. Nothing is sent to anyone, and it never "
                   "changes a status or marks anything Resolved.")
ui.show_flash()
threshold = st.slider("Days without an update before follow-up", 1, 14, config.FOLLOWUP_THRESHOLD_DAYS, key="fu_threshold")

open_reports = ui.guarded(db.list_open, 1000)
if open_reports is None:
    st.stop()
decisions = evaluate_followups(open_reports, threshold)
if not decisions:
    st.info("There are no open reports to follow up on.")
    st.stop()
if len(open_reports) >= 1000:
    st.caption("Showing the 1000 most recent open reports.")

due = [d for d in decisions if d["action"] != "none"]
m1, m2, m3 = st.columns(3)
m1.metric("Open reports checked", len(decisions))
m2.metric("Recommended reminders", sum(d["action"] == "remind" for d in decisions))
m3.metric("Recommended escalations", sum(d["action"] == "escalate" for d in decisions))

ui.table(pd.DataFrame([{"ID": d["complaint_id"], "Recommendation": d["recommendation"],
                        "Priority": d["priority"], "Persisted status (simulated)": d["persisted_status"],
                        "Days since update": d["stale_days"], "Reason": d["reason"]} for d in decisions]))
st.caption(STATUS_NOTE)
if any(d.get("error") for d in decisions):
    st.warning("Some reports have invalid timestamps and could not be evaluated.")

if not due:
    st.success("All open reports were updated recently. Nothing to follow up.")
    st.stop()

st.subheader("Drafted messages (simulated)")
for d in due:
    with st.expander(f"{d['recommendation']} · {d['complaint_id']} · {d['stale_days']} days without update"):
        st.write(d["reason"])
        st.text(draft_message(d))

if st.button("Record these recommendations in the activity log (simulated)"):
    try:
        n = record_followups(due)
    except db.DatabaseError as exc:
        st.error(f"⚠️ Nothing was recorded: {exc}")
    else:
        skipped = len(due) - n
        msg = f"Logged {n} new simulated follow-up event(s)."
        if skipped:
            msg += f" {skipped} were already logged earlier and were not repeated."
        st.success(msg + " No messages were sent.")
