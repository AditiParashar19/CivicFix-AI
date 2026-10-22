"""Dashboard: filters drive the metrics, charts and table together (all computed by the database)."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import config
import ui
import security
from database import db

security.require_roles({"reviewer", "operator", "admin"})

try:
    import plotly.express as px
except ImportError:  # charts fall back to Streamlit's built-ins
    px = None

st.title("📊 Dashboard")
ui.show_flash()

all_time = ui.guarded(db.get_stats)
if all_time is None:
    st.stop()
if all_time["total"] == 0:
    st.info("No complaints yet. Report an issue to see the dashboard.")
    st.page_link("pages/submit_complaint.py", label="Report an issue", icon="📝")
    st.stop()

with st.expander("Filters", expanded=True):
    f1, f2, f3 = st.columns(3)
    sel_status = f1.multiselect("Status", config.STATUSES, key="dash_status")
    sel_cat = f2.multiselect("Category", config.CATEGORIES, key="dash_cat", format_func=config.label)
    sel_pri = f3.multiselect("Priority", config.PRIORITIES, key="dash_pri")
    g1, g2 = st.columns([2, 1])
    query = g1.text_input("Search", key="dash_query", placeholder="ID, location, description or department (all words must match)")
    sort_label = g2.selectbox("Sort by", ["Newest", "Oldest", "Recently updated", "Highest priority"], key="dash_sort")
    use_dates = st.checkbox("Filter by created date", key="dash_use_dates")
    d_from = d_to = None
    if use_dates:
        h1, h2 = st.columns(2)
        d_from = h1.date_input("From", value=date.today() - timedelta(days=30), key="dash_from")
        d_to = h2.date_input("To", value=date.today(), key="dash_to")
    st.caption("Leave a filter empty to include everything. Filters and search combine (AND).")

filters = dict(status=sel_status or None, category=sel_cat or None, priority=sel_pri or None,
               search=query.strip() or None, created_from=d_from, created_to=d_to)
sort = {"Newest": "newest", "Oldest": "oldest", "Recently updated": "updated", "Highest priority": "priority"}[sort_label]
is_filtered = any(v for v in filters.values())

with st.spinner("Loading…"):
    stats = ui.guarded(db.get_stats, **filters)
if stats is None:
    st.stop()

st.markdown(f"**{'Filtered results' if is_filtered else 'All reports'}** · {stats['total']} of {all_time['total']} total")
ui.metrics_row(stats)
st.caption("Pending = Submitted + Under Review; In Progress is counted separately; "
           "Total = Pending + In Progress + Resolved. Charts and the table below use the same filters.")
if stats["total"] == 0:
    st.info("No complaints match these filters. Try removing a filter or shortening the search.")
    st.stop()


def counts_chart(field: str, order: list, colors: dict, title: str) -> None:
    raw = ui.guarded(db.count_by, field, default=None, **filters)
    if raw is None:
        return
    df = pd.DataFrame({field: order, "reports": [raw.get(k, 0) for k in order]})
    if px is None:
        st.caption(title)
        st.bar_chart(df.set_index(field)["reports"])
        return
    fig = px.bar(df, x=field, y="reports", color=field, color_discrete_map=colors, title=title)
    fig.update_layout(showlegend=False, height=300, margin=dict(l=10, r=10, t=50, b=10), xaxis_title=None)
    ui.plotly(fig)


c1, c2 = st.columns(2)
with c1:
    counts_chart("category", config.CATEGORIES, ui.CATEGORY_COLORS, "Reports by category")
with c2:
    counts_chart("status", config.STATUSES, ui.STATUS_COLORS, "Reports by status")
counts_chart("priority", config.PRIORITIES, ui.PRIORITY_COLORS, "Reports by priority")

st.subheader("Complaints")
size = st.selectbox("Rows per page", [10, 25, 50], index=1, key="dash_size")
signature = (repr(sorted((k, str(v)) for k, v in filters.items())), sort, size)
page = ui.current_page("dash", signature)
res = ui.guarded(db.search_complaints, sort=sort, page=page, page_size=size, **filters)
if res is None:
    st.stop()
ui.table(pd.DataFrame([{"ID": r["complaint_id"], "Category": config.label(r["category"]), "Location": r["location"],
                        "Priority": f"{ui.PRIORITY_ICON[r['priority']]} {r['priority']}", "Status": r["status"],
                        "Department (mock)": r["department"], "Created": ui.fmt_ts(r["created_at"]),
                        "Updated": ui.fmt_ts(r["updated_at"])} for r in res["rows"]]))
ui.page_controls("dash", res["page"], res["pages"], res["total"])
st.caption("Open **Complaint History** for full details, the status timeline and agent decisions.")
