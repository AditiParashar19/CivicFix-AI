"""CivicFix AI: Streamlit entry point.   Run:  streamlit run app.py"""
import pandas as pd
import streamlit as st

st.set_page_config(page_title="CivicFix AI", page_icon="🏙️", layout="wide")

import config  # noqa: E402  (after set_page_config)
import ui  # noqa: E402
import security  # noqa: E402
from database import db  # noqa: E402
from database.seed import seed_database  # noqa: E402


def home() -> None:
    principal = security.require_roles(set(security.ROLES))
    ui.show_flash()
    owner_id = None if principal.is_staff else principal.subject
    stats = ui.guarded(db.get_stats, owner_id=owner_id)
    if stats is None:
        return

    # Landing page: resident/admin entry points, with working Streamlit navigation.
    st.markdown("""
    <div class="cp-topbar">
      <div class="cp-brand"><div class="cp-logo">🏙️</div><div><div class="cp-brand-name">Civic<span>Fix</span> AI</div><div class="cp-tagline">Report it. Track it. See it fixed.</div></div></div>
      <div class="cp-topnote">Civic issue management</div>
    </div>
    <section class="cp-hero">
      <span class="cp-eyebrow">Community-powered, AI-assisted</span>
      <h1>Report a problem. We route it.</h1>
      <p>Report potholes, garbage, broken streetlights and water leaks. Track every report from submission to resolution.</p>
      <div class="cp-pills">
        <div>⚡ AI categorization</div><div>🎯 Department routing</div>
        <div>🔎 Duplicate detection</div><div>📊 Status tracking</div>
      </div>
    </section>
    """, unsafe_allow_html=True)

    resident, admin = st.columns(2, gap="large")
    with resident:
        st.markdown("""
        <div class="cp-role-card cp-resident">
          <div class="cp-role-icon">👥</div><h2>I am a resident</h2>
          <p>Report civic issues, track their status, and help improve your community.</p>
          <ul><li>✏️ Submit complaints with details</li><li>🔍 Track complaint status</li><li>🔔 Review updates and recommendations</li><li>🛡️ Help build a cleaner city</li></ul>
        </div>
        """, unsafe_allow_html=True)
        ui.nav_button("Report an issue", "pages/submit_complaint.py", key="home_report", icon="📝")
    with admin:
        st.markdown("""
        <div class="cp-role-card cp-admin">
          <div class="cp-role-icon">🛡️</div><h2>I am staff</h2>
          <p>Review and manage civic complaints with intelligent tools.</p>
          <ul><li>⚙️ View and manage complaints</li><li>👥 AI-assisted categorization and prioritization</li><li>📌 Review department recommendations</li><li>📈 Track progress and activity</li></ul>
        </div>
        """, unsafe_allow_html=True)
        if principal.is_staff:
            ui.nav_button("Show the dashboard", "pages/dashboard.py", key="home_dashboard", icon="📊")
        else:
            st.info("Staff tools are available only to authorized staff accounts.")

    st.markdown('<div class="cp-stats-heading">Civic issue overview</div>', unsafe_allow_html=True)
    metrics = st.columns(4, gap="medium")
    metric_items = [("📨", stats["total"], "Total reports", "blue"),
                    ("🟢", stats["resolved"], "Resolved reports", "green"),
                    ("⏱️", stats["pending"], "Pending reports", "purple"),
                    ("📋", stats["in_progress"], "In progress", "amber")]
    for col, (icon, value, label, color) in zip(metrics, metric_items):
        with col:
            st.markdown(f'<div class="cp-stat cp-{color}"><div class="cp-stat-icon">{icon}</div><div><div class="cp-stat-value">{value}</div><div class="cp-stat-label">{label}</div></div></div>', unsafe_allow_html=True)

    st.markdown('<div class="cp-footer">Together for a better, cleaner and smarter tomorrow 🌱<br><span>Prototype: routing, reminders and status changes are simulated; no real department is contacted.</span></div>', unsafe_allow_html=True)

    with st.expander("View complaint analytics and agent workflow"):
        ui.simulation_note()
        # Performance: the expander body always runs on a rerun, so the two count queries are loaded on demand.
        if st.toggle("Load charts", key="home_load_charts"):
            a1, a2 = st.columns(2)
            for col, field, order, title in ((a1, "category", config.CATEGORIES, "Reports by category"),
                                             (a2, "priority", config.PRIORITIES, "Reports by priority")):
                counts = ui.guarded(db.count_by, field, default={}, owner_id=owner_id)
                with col:
                    st.markdown(f"**{title}**")
                    st.bar_chart(pd.Series({(config.label(k) if field == "category" else k): counts.get(k, 0)
                                            for k in order}), color="#0f766e")
        if principal.is_staff:
            st.subheader("How the agents work")
            cols = st.columns(4)
            steps = [("1. Intake Agent", "Classifies issues, scores priority and drafts a complaint (LLM second opinion when configured)."),
                     ("2. Duplicate Agent", "Compares text, location and category; an LLM re-checks borderline matches."),
                     ("3. Planning Agent", "Recommends a department and next steps, plus AI tips for the citizen."),
                     ("4. Follow-up Agent", "Identifies stalled reports and drafts reminders or escalations.")]
            for col, (title, description) in zip(cols, steps):
                with col:
                    st.markdown(f"**{title}**")
                    st.caption(description)
            st.page_link("pages/activity_log.py", label="Open Agent Activity Log", icon="🧾")


def demo_controls() -> None:
    with st.expander("Demo controls (fictional data)"):
        st.caption("Sample reports are fictional and flagged as demo data. Real reports are never deleted here.")
        if st.button("Load sample data (only if empty)"):
            try:
                n = seed_database()
                ui.flash(f"Loaded {n} fictional sample reports." if n else
                         "Nothing loaded: reports already exist. Demo data is never mixed into existing data.",
                         "success" if n else "warning")
                st.rerun()
            except db.DatabaseError as exc:
                st.error(f"Could not load sample data: {exc}")
        confirm = st.checkbox("Replace existing DEMO records with a fresh sample set")
        if st.button("Replace demo records", disabled=not confirm):
            try:
                n = seed_database(reset=True)
                st.session_state.pop("workflow", None)
                ui.flash(f"Demo records replaced ({n} fictional reports). Real reports were not touched.")
                st.rerun()
            except db.DatabaseError as exc:
                st.error(f"Could not reset demo data: {exc}")


ui.inject_css()
principal = security.current_principal()
if principal is None:
    security.show_login_or_denial()
    st.stop()
if config.IS_PRODUCTION and not config.MONGODB_URI:
    st.error("Production mode requires the configured shared MongoDB database. Set MONGODB_URI before startup.")
    st.stop()

storage = ui.ensure_ready()

pages = [
    st.Page(home, title="Home", icon="🏠", default=True),
    st.Page("pages/submit_complaint.py", title="Report an Issue", icon="📝"),
    st.Page("pages/complaint_history.py", title="My Complaints" if principal.role == "citizen" else "Complaint History", icon="📁"),
]
if principal.is_staff:
    pages.append(st.Page("pages/dashboard.py", title="Dashboard", icon="📊"))
    pages.append(st.Page("pages/activity_log.py", title="Agent Activity Log", icon="🧾"))
if principal.role in {"operator", "admin"}:
    pages.append(st.Page("pages/followup.py", title="Follow-up Agent", icon="⏰"))
nav = st.navigation(pages)

with st.sidebar:
    st.caption(f"CivicFix AI · {principal.role.title()} · prototype")
    if principal.role != "citizen":
        ui.storage_banner(storage)
    if config.IS_PRODUCTION:
        st.caption(principal.display_name)
        if st.button("Sign out"):
            st.logout()
    elif principal.role == "admin":
        st.caption("Local development access (admin); use fictional data only.")
    if st.button("🔄 Refresh data"):
        st.rerun()
    if config.ENABLE_DEMO_CONTROLS:
        demo_controls()

nav.run()