"""Execute every page script against a real (temporary) database using the recording Streamlit stub.

These tests exercise OUR page logic: guards, data flow, state handling, error paths. They do not prove that real
Streamlit renders correctly; see test_apptest.py for that (runs when Streamlit is installed)."""
import config
import pytest

from conftest import REC
from database import db
from database.seed import seed_database
from stub_streamlit import Runtime, run_page, streamlit_stub


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ui.db"))
    db._cache.clear()
    db.init_db()
    seed_database()
    yield
    db._cache.clear()


@pytest.fixture
def empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ui-empty.db"))
    db._cache.clear()
    db.init_db()
    yield
    db._cache.clear()


PAGES = ["pages/dashboard.py", "pages/complaint_history.py", "pages/followup.py", "pages/activity_log.py",
         "pages/submit_complaint.py"]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_with_data(seeded, page):
    rt = Runtime()
    with streamlit_stub(rt):
        run_page(rt, page)
    assert not [e for e in rt.errors if e.startswith("error")], rt.errors


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_on_an_empty_database(empty, page):
    rt = Runtime()
    with streamlit_stub(rt):
        run_page(rt, page)
    assert not [e for e in rt.errors if e.startswith("error")], rt.errors


def test_home_page_shows_live_counts_that_follow_the_definitions(seeded):
    rt = Runtime()
    with streamlit_stub(rt):
        run_page(rt, "app.py")
    shown = {c[1][0]: c[1][1] for c in rt.calls if c[0] == "metric"}
    s = db.get_stats()
    assert shown["Total"] == s["total"] == 14
    assert shown["Pending"] == s["submitted"] + s["under_review"] and shown["In Progress"] == s["in_progress"]
    assert shown["Pending"] + shown["In Progress"] + shown["Resolved"] == shown["Total"]
    assert not [e for e in rt.errors if e.startswith("error")], rt.errors


def test_home_page_empty_state_and_no_unsafe_html_for_citizen_text(empty):
    db.insert_complaint({**REC, "location": "<img src=x onerror=alert(1)>"})
    rt = Runtime()
    with streamlit_stub(rt):
        run_page(rt, "app.py")
    html_calls = [c for c in rt.calls if c[0] == "markdown" and c[2].get("unsafe_allow_html")]
    assert all("onerror" not in str(c[1]) for c in html_calls)         # citizen text never goes through unsafe HTML


def test_dashboard_metrics_and_table_follow_the_filters(seeded):
    rt = Runtime({"dash_cat": ["pothole"], "dash_status": ["Resolved", "Submitted"]})
    with streamlit_stub(rt):
        run_page(rt, "pages/dashboard.py")
    expected = db.get_stats(category="pothole", status=["Resolved", "Submitted"])
    shown = {c[1][0]: c[1][1] for c in rt.calls if c[0] == "metric"}
    assert shown["Total"] == expected["total"] and shown["Pending"] == expected["pending"]
    table = rt.tables[-1]
    assert len(table) == min(expected["total"], 25) and set(table["Category"]) <= {"Pothole"}


def test_dashboard_search_filters_the_table(seeded):
    rt = Runtime({"dash_query": "pothole market"})
    with streamlit_stub(rt):
        run_page(rt, "pages/dashboard.py")
    assert 1 <= len(rt.tables[-1]) == db.search_complaints(search="pothole market")["total"]


def test_dashboard_search_without_matches_shows_empty_state(seeded):
    rt = Runtime({"dash_query": "zzzz-nothing"})
    with streamlit_stub(rt):
        run_page(rt, "pages/dashboard.py")
    assert any("No complaints match" in t for t in rt.texts) and not rt.tables


def test_history_search_and_selection_follow_current_results(seeded):
    rt = Runtime({"hist_q": "garbage"})
    with streamlit_stub(rt):
        run_page(rt, "pages/complaint_history.py")
    ids = list(rt.tables[0]["ID"])
    assert ids and all(db.get_complaint(i)["category"] == "garbage" for i in ids)
    assert rt.session_state["hist_selected"] in ids                      # selection reset into the visible results


def test_history_status_update_requires_confirmation_and_follows_the_rules(seeded):
    cid = db.search_complaints(status="Submitted")["rows"][0]["complaint_id"]
    rt = Runtime({"hist_q": cid, "I confirm this simulated status change.": False, "Move to Under Review": True})
    with streamlit_stub(rt):
        run_page(rt, "pages/complaint_history.py")
    assert db.get_complaint(cid)["status"] == "Submitted"                # no confirmation -> no change
    rt = Runtime({"hist_q": cid, "I confirm this simulated status change.": True, "Move to Under Review": True,
                  "Note (optional)": "checked"})
    with streamlit_stub(rt):
        run_page(rt, "pages/complaint_history.py")
    assert db.get_complaint(cid)["status"] == "Under Review"
    assert db.get_status_history(cid)[-1]["note"] == "checked"


def test_followup_page_records_once_even_if_clicked_twice(seeded):
    label = "Record these recommendations in the activity log (simulated)"
    before = db.count_activity(step=["reminder_drafted", "escalation_drafted"])
    for _ in range(2):
        rt = Runtime({label: True, "fu_threshold": 1})
        with streamlit_stub(rt):
            run_page(rt, "pages/followup.py")
        counts = db.count_activity(step=["reminder_drafted", "escalation_drafted"])
        if _ == 0:
            first = counts
    assert first > before and counts == first


def test_activity_page_filters_by_agent(seeded):
    rt = Runtime({"act_agents": ["Planning Agent"]})
    with streamlit_stub(rt):
        run_page(rt, "pages/activity_log.py")
    assert rt.tables and set(rt.tables[0]["Agent"]) == {"Planning Agent"}


def test_submit_flow_saves_nothing_until_confirmed_then_saves_once(empty):
    desc = "Large pothole on Sector 4 Main Road near the City Market bus stop, bikes swerving"
    analyze = {"What is the problem?": desc, "Where is it?": "Sector 4 Main Road",
               "Step 3 · Run the analysis agents": True}
    rt = Runtime(analyze)
    with streamlit_stub(rt):
        run_page(rt, "pages/submit_complaint.py")
        assert "workflow" in rt.session_state and db.get_stats()["total"] == 0        # analysed, NOT saved
        # confirm button disabled until the review box is ticked
        rt.inputs = {"Confirm and save report": True}
        run_page(rt, "pages/submit_complaint.py")
        assert db.get_stats()["total"] == 0
        rt.inputs = {"Confirm and save report": True,
                     "I have reviewed the category, priority, duplicates and draft, and I want to save this report.": True}
        run_page(rt, "pages/submit_complaint.py")
        assert db.get_stats()["total"] == 1 and "workflow" not in rt.session_state


def test_submit_requires_duplicate_acknowledgement(seeded):
    seed = db.search_complaints(category="pothole", open_only=True)["rows"][0]
    inputs = {"What is the problem?": seed["description"], "Where is it?": seed["location"],
              "Step 3 · Run the analysis agents": True}
    rt = Runtime(inputs)
    with streamlit_stub(rt):
        run_page(rt, "pages/submit_complaint.py")
        assert rt.session_state["workflow"]["duplicates"]["is_potential_duplicate"]
        before = db.get_stats()["total"]
        rt.inputs = {"Confirm and save report": True,
                     "I have reviewed the category, priority, duplicates and draft, and I want to save this report.": True}
        run_page(rt, "pages/submit_complaint.py")
        assert db.get_stats()["total"] == before                                      # blocked: duplicates not acknowledged
        rt.inputs = {"Confirm and save report": True, "dup_ack": True,
                     "I have reviewed the category, priority, duplicates and draft, and I want to save this report.": True}
        run_page(rt, "pages/submit_complaint.py")
        assert db.get_stats()["total"] == before + 1


def test_pages_report_storage_failure_instead_of_empty_results(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path))            # a directory: cannot be opened as a database
    db._cache.clear()
    for page in ("pages/dashboard.py", "pages/complaint_history.py", "pages/activity_log.py"):
        rt = Runtime()
        with streamlit_stub(rt):
            run_page(rt, page)
        assert any("Storage problem" in e for e in rt.errors), (page, rt.errors)
    db._cache.clear()
