"""Shared Streamlit helpers: design tokens, badges, guarded data access, pagination, storage set-up.

Design rules: citizen-supplied text is ALWAYS rendered as text (st.text / dataframes / escaped), never as
trusted HTML. HTML is only built from fixed constants and html.escape()d values.
Reads are never cached: every rerun reads the configured data source, so views cannot go stale after a write.
Styling lives in assets/civicfix.css plus the fixes in _FIXES_CSS (loaded once by inject_css).
"""
import functools
import html
import inspect
from typing import Any, Callable, Optional

import streamlit as st

import config
from database import db
from database.seed import seed_database

# --- colour / design tokens -----------------------------------------------------------------------
STATUS_COLORS = {"Submitted": "#455a64", "Under Review": "#1565c0", "In Progress": "#b45309", "Resolved": "#2e7d32"}
PRIORITY_COLORS = {"Low": "#546e7a", "Medium": "#b45309", "High": "#c62828", "Critical": "#7f1d1d"}
CATEGORY_COLORS = {"pothole": "#8d6e63", "garbage": "#558b2f", "broken streetlight": "#f9a825",
                   "water leakage": "#0277bd", "other": "#78909c"}
PRIORITY_ICON = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Critical": "🔴"}   # always paired with the text label

CSS_PATH = config.ROOT / "assets" / "civicfix.css"

# Small fixes applied on top of civicfix.css. Kept in code (not a second file) so they always load.
_FIXES_CSS = """
/* Header: Streamlit's top bar is position:fixed, so content slid underneath it and got covered.
   Make it scroll away WITH the page (absolute) and keep the content clear of it. */
header[data-testid="stHeader"], [data-testid="stHeader"] {
  position: absolute !important; top: 0; left: 0; right: 0; background: transparent !important; box-shadow: none;
}
.block-container, [data-testid="stMainBlockContainer"] { padding-top: 4rem !important; }
.cp-hero { overflow: hidden; isolation: isolate; }
.cp-hero > * { position: relative; z-index: 1; }
.cp-hero::after { z-index: 0; }
.cp-role-card { overflow: hidden; }
.cp-role-card::after { content: ""; display: table; clear: both; }

/* Buttons: make the main actions stand out */
.stButton > button[kind="primary"], [data-testid="stBaseButton-primary"] {
  min-height: 3.1rem; font-size: 1.05rem; font-weight: 700; letter-spacing: .01em; color: #fff;
  background: var(--cf-teal, #0f766e); border: 2px solid var(--cf-teal-dark, #0b5a54); border-radius: 10px;
  box-shadow: 0 6px 16px rgba(15,118,110,.35); transition: transform .15s ease, box-shadow .15s ease;
}
.stButton > button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  background: var(--cf-teal-dark, #0b5a54); color: #fff; transform: translateY(-2px);
  box-shadow: 0 10px 22px rgba(15,118,110,.4);
}

/* Sidebar: widgets such as 'Demo controls' were white boxes holding light text */
[data-testid="stSidebar"] [data-testid="stExpander"] { background: rgba(255,255,255,.06); border: 1px solid #4a5b6c; }
[data-testid="stSidebar"] [data-testid="stExpander"] details { background: transparent; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * { color: #c3cfdb !important; }
[data-testid="stSidebar"] .stTextInput input, [data-testid="stSidebar"] [data-baseweb="select"] > div {
  background: #223344 !important; color: #fff !important; border-color: #4a5b6c !important; }
"""


@functools.lru_cache(maxsize=1)
def _css() -> str:
    """Read the stylesheet once per process (not on every rerun) and append the fixes."""
    try:
        base = CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        base = ""
    return base + _FIXES_CSS


def inject_css() -> None:
    """Load the base stylesheet plus the fixes. The app still works unstyled."""
    st.markdown(f"<style>{_css()}</style>", unsafe_allow_html=True)


def nav_button(label: str, page: str, key: str, icon: str | None = None) -> None:
    """A real full-width primary (purple) button that opens another page. Styled by the theme itself,
    so it still looks like a button even if the extra stylesheet fails to load."""
    if st.button(label, key=key, type="primary", icon=icon, **_stretch(st.button)):
        st.switch_page(page)


def badge(text: str, color: str) -> str:
    """HTML badge. Text and colour come from fixed constants; both are escaped anyway."""
    return f'<span class="badge" style="background:{html.escape(color)}">{html.escape(text)}</span>'


def status_badge(status: str) -> str:
    return badge(status, STATUS_COLORS[status]) if status in STATUS_COLORS else ""


def priority_badge(priority: str) -> str:
    return badge(priority, PRIORITY_COLORS[priority]) if priority in PRIORITY_COLORS else ""


def simulation_note(text: str = "") -> None:
    """``text`` must be a trusted constant (it is rendered as HTML)."""
    msg = text or ("<b>Prototype:</b> routing, reminders and status changes are <b>simulated</b>. "
                   "No real department is contacted, no notification is sent and no real complaint is filed.")
    st.markdown(f'<div class="sim-note">{msg}</div>', unsafe_allow_html=True)


def fmt_ts(iso: Optional[str]) -> str:
    return (iso or "")[:16].replace("T", " ")


def step_label(n: int, text: str) -> None:
    st.markdown(f'<div class="cf-step">Step {n}</div>', unsafe_allow_html=True)
    st.subheader(text)


# --- NEW: visual components ----------------------------------------------------------------------
def status_tracker(status: str) -> None:
    """Horizontal 'lane': Submitted -> Under Review -> In Progress -> Resolved."""
    if status not in config.STATUSES:
        return
    current = config.STATUSES.index(status)
    items = []
    for i, name in enumerate(config.STATUSES):
        cls = "done" if i < current or status == "Resolved" else "current" if i == current else ""
        items.append(f'<li class="{cls}">{html.escape(name)}</li>')
    st.markdown(f'<ol class="cf-lane" aria-label="Status: {html.escape(status)}">{"".join(items)}</ol>',
                unsafe_allow_html=True)


def complaint_card(c: dict) -> None:
    """Compact card for lists. ``c`` is a complaint record from db.search_complaints."""
    pr = str(c.get("priority", "")).lower()
    pr_cls = pr if pr in {"low", "medium", "high", "critical"} else "low"
    summary = html.escape(str(c.get("summary") or c.get("description", ""))[:140])
    st.markdown(
        f'<div class="cf-card cf-p-{pr_cls}">'
        f'<div class="cf-card-head"><span class="cf-card-id">{html.escape(str(c.get("complaint_id", "")))}</span>'
        f'{priority_badge(str(c.get("priority", "")))} {status_badge(str(c.get("status", "")))}</div>'
        f'<div class="cf-card-title">{summary}</div>'
        f'<div class="cf-card-meta">{html.escape(config.label(str(c.get("category", "other"))))} · '
        f'{html.escape(str(c.get("location", "")))} · {html.escape(fmt_ts(c.get("created_at")))}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# --- version-tolerant wrappers (Streamlit renamed use_container_width -> width="stretch") -------------
def _stretch(fn: Callable) -> dict:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if "width" in params:
        return {"width": "stretch"}
    return {"use_container_width": True} if "use_container_width" in params else {}


def table(data: Any, **kw) -> None:
    st.dataframe(data, hide_index=True, **_stretch(st.dataframe), **kw)


def plotly(fig) -> None:
    st.plotly_chart(fig, **_stretch(st.plotly_chart))


# --- guarded data access -------------------------------------------------------------------------
def guarded(fn: Callable, *args, default=None, **kw):
    """Call a data function; on failure show a clear error and return ``default`` (never fake results)."""
    try:
        return fn(*args, **kw)
    except db.DatabaseError as exc:
        st.error(f"⚠️ Storage problem: {exc}")
        st.caption("Nothing was changed. Check the storage connection (see the sidebar) and try again.")
    except ValueError as exc:
        st.warning(f"Please adjust your filters: {exc}")
    return default


def flash(message: str, kind: str = "success") -> None:
    """Queue a message that survives the next st.rerun()."""
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        {"success": st.success, "warning": st.warning, "error": st.error}.get(item[0], st.info)(item[1])


# --- pagination ----------------------------------------------------------------------------------
def current_page(key: str, signature) -> int:
    """Page number for ``key``; resets to 1 whenever the filter ``signature`` changes (no stale pages)."""
    if st.session_state.get(f"{key}__sig") != signature:
        st.session_state[f"{key}__sig"] = signature
        st.session_state[f"{key}__page"] = 1
    return int(st.session_state.get(f"{key}__page", 1))


def page_controls(key: str, page: int, pages: int, total: int) -> None:
    st.session_state[f"{key}__page"] = page            # the backend may have clamped it
    c1, c2, c3 = st.columns([1, 2, 1])
    if c1.button("◀ Previous", key=f"{key}_prev", disabled=page <= 1):
        st.session_state[f"{key}__page"] = page - 1
        st.rerun()
    c2.caption(f"Page {page} of {pages} · {total} result{'s' if total != 1 else ''}")
    if c3.button("Next ▶", key=f"{key}_next", disabled=page >= pages):
        st.session_state[f"{key}__page"] = page + 1
        st.rerun()


# --- metrics -------------------------------------------------------------------------------------
def metrics_row(stats: dict, suffix: str = "") -> None:
    cols = st.columns(6)
    items = [("Total", "total", None), ("Pending", "pending", "Submitted + Under Review"),
             ("Submitted", "submitted", None), ("Under Review", "under_review", None),
             ("In Progress", "in_progress", "Counted separately from Pending"), ("Resolved", "resolved", None)]
    for col, (label, key, help_) in zip(cols, items):
        col.metric(label + suffix, stats[key], help=help_)


# --- storage -------------------------------------------------------------------------------------
@st.cache_resource(show_spinner="Connecting to storage…")
def _init_storage() -> dict:
    """Runs once per server process (failures are not cached, so a fixed config recovers on the next run)."""
    db.init_db()
    if config.AUTO_SEED and db.get_stats()["total"] == 0:
        seed_database()
    return db.describe_backend()


def ensure_ready() -> dict:
    try:
        return _init_storage()
    except Exception as exc:
        st.error(f"⚠️ CivicFix AI cannot reach its storage: {exc}")
        st.markdown("Check `MONGODB_URI` / `MONGODB_DATABASE` (or `CIVICFIX_DB_PATH`) in your `.env` and the "
                    "troubleshooting section of the README, then reload this page.")
        if st.button("Retry connection"):
            st.rerun()
        st.stop()


def storage_banner(info: dict) -> None:
    if info["kind"] == "mongodb":
        st.caption(f"🗄️ Storage: MongoDB · database `{info['detail']}` · shared & persistent")
    else:
        st.caption(f"🗄️ Storage: {info['label']} (`{info['detail']}`)")
        st.caption("Local file: fine for development and demos. On hosts with temporary disks it is lost on "
                   "redeploy, and it is not shared between servers. Set `MONGODB_URI` for persistent, shared storage.")