"""Real Streamlit rendering tests (streamlit.testing.v1.AppTest). Skipped automatically when Streamlit is not
installed. These are the authoritative UI checks; test_pages_stub.py only checks page logic."""
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

import config  # noqa: E402
from database import db  # noqa: E402
from database.seed import seed_database  # noqa: E402

TIMEOUT = 30
APP = str(Path(__file__).resolve().parents[1] / "app.py")   # AppTest resolves relative paths against tests/

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICFIX_DB_PATH", str(tmp_path / "apptest.db"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "apptest.db"))
    monkeypatch.setattr(config, "MONGODB_URI", "")
    db._cache.clear()
    db.init_db()
    seed_database()
    return st_testing.AppTest.from_file(APP, default_timeout=TIMEOUT)



def test_home_renders_metrics_without_exceptions(app):
    at = app.run()
    assert not at.exception
    labels = {m.label for m in at.metric}
    assert {"Total", "Pending", "In Progress", "Resolved"} <= labels


def test_submit_page_requires_confirmation_before_saving(app):
    at = app.run()
    at.switch_page("pages/submit_complaint.py").run()
    # Form edits are held in the browser until submit. Running AppTest between
    # field edits simulates a rerun and discards those unsubmitted form values.
    at.text_area[0].input("Large pothole near the new school gate, a child fell yesterday")
    at.text_input[0].input("School Road gate")
    before = db.get_stats()["total"]
    next(b for b in at.button if "Run the analysis" in b.label).click().run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert "workflow" in at.session_state, [m.value for m in at.markdown][:8]
    assert db.get_stats()["total"] == before
    save = next(b for b in at.button if b.label == "Confirm and save report")
    assert save.disabled
