import pytest

from agents.orchestrator import run_intake_workflow, save_report
from database import db
from services.validation import validate_image, validate_report_input

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "w.db")
    db.init_db(p)
    return p


def run(desc, loc, path):
    return run_intake_workflow(desc, loc, use_llm=False, db_path=path)


def test_invalid_input_returns_friendly_errors(path):
    r = run("short", "", path)
    assert not r["ok"] and len(r["errors"]) == 2


def test_nothing_is_saved_before_human_confirmation(path):
    run("Large pothole on Sector 4 Main Road near the bus stop", "Sector 4 Main Road", path)
    assert db.get_stats(path)["total"] == 0


def test_end_to_end_and_duplicate_gate(path):
    w = run("Large pothole on Sector 4 Main Road near the bus stop, bike slipped", "Sector 4 Main Road", path)
    assert w["ok"] and w["plan"]["requires_human_review"] and w["plan"]["simulated"]
    cid = save_report(w, db_path=path)
    log = db.get_activity(cid, db_path=path)
    assert {"Orchestrator", "Intake Agent", "Duplicate Agent", "Planning Agent", "Human Review"} <= {
        e["agent"] for e in log}

    w2 = run("Big pothole on Sector 4 Main Road near bus stop, bike slipped", "Sector 4 Main Road", path)
    assert w2["duplicates"]["is_potential_duplicate"]
    with pytest.raises(ValueError):
        save_report(w2, db_path=path)                      # must acknowledge first
    cid2 = save_report(w2, duplicate_acknowledged=True, db_path=path)   # legitimate submissions allowed
    assert cid2 != cid


def test_edited_draft_is_stored_and_logged(path):
    w = run("Garbage overflowing from the bin near the market gate", "Market Gate", path)
    cid = save_report(w, draft="My own edited letter", db_path=path)
    assert db.get_complaint(cid, path)["draft"] == "My own edited letter"
    assert any(e["step"] == "edit_draft" for e in db.get_activity(cid, db_path=path))


def test_image_validation():
    assert validate_image("a.png", PNG) == []
    assert validate_image("a.exe", b"MZ") != []
    assert validate_image("a.png", b"not really a png") != []
    assert validate_image("a.png", PNG + b"0" * (6 * 1024 * 1024)) != []
    assert validate_image("a.png", b"") != []


def test_save_with_image_and_rejects_bad_image(path, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    w = run("Water pipe burst near the community hall, road waterlogged", "Community Hall", path)
    with pytest.raises(ValueError):
        save_report(w, image_name="x.png", image_bytes=b"junk", db_path=path)
    assert db.get_stats(path)["total"] == 0
    cid = save_report(w, image_name="photo.png", image_bytes=PNG, db_path=path)
    assert db.get_complaint(cid, path)["image_path"].endswith(f"{cid}.png")


def test_report_input_validation_messages():
    assert validate_report_input("x" * 20, "Main Road") == []
    assert len(validate_report_input("x" * 3000, "Main Road")) == 1


# ---- human-in-the-loop boundary, idempotency and failure handling ------------------------------------
DESC, LOC = "Large pothole on Sector 4 Main Road near the bus stop, bike slipped", "Sector 4 Main Road"


def test_analysis_never_writes_and_save_requires_a_valid_workflow(path):
    w = run(DESC, LOC, path)
    assert db.get_stats(path)["total"] == 0 and db.count_activity(db_path=path) == 0
    with pytest.raises(ValueError):
        save_report({"ok": False}, db_path=path)
    with pytest.raises(ValueError):
        save_report({**w, "workflow_id": None}, db_path=path)
    assert db.get_stats(path)["total"] == 0


def test_double_click_or_rerun_saves_once(path):
    w = run(DESC, LOC, path)
    first, second = save_report(w, db_path=path), save_report(w, db_path=path)
    assert first == second and db.get_stats(path)["total"] == 1
    assert len([e for e in db.get_activity(first, db_path=path) if e["step"] == "confirm_save"]) == 1


def test_duplicate_acknowledgement_is_required_and_recorded(path):
    save_report(run(DESC, LOC, path), db_path=path)
    w2 = run("Big pothole on Sector 4 Main Road near bus stop, bike slipped", LOC, path)
    assert w2["duplicates"]["is_potential_duplicate"] and w2["duplicates"]["matches"][0]["complaint_id"]
    with pytest.raises(ValueError):
        save_report(w2, db_path=path)
    assert db.get_stats(path)["total"] == 1                       # refused: nothing written
    cid = save_report(w2, duplicate_acknowledged=True, db_path=path)
    assert any(e["step"] == "duplicate_override" for e in db.get_activity(cid, db_path=path))


def test_similar_report_filed_after_analysis_is_caught_at_save_time(path):
    stale = run(DESC, LOC, path)                                  # analysed while the table was empty
    save_report(run(DESC, LOC, path), db_path=path)               # someone else files the same issue meanwhile
    assert not stale["duplicates"]["is_potential_duplicate"]
    with pytest.raises(ValueError):
        save_report(stale, db_path=path)
    assert db.get_stats(path)["total"] == 1


def test_photo_failure_after_save_is_reported_as_partial_not_lost(path, tmp_path, monkeypatch):
    import agents.orchestrator as orch
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(orch, "save_image", boom)
    with pytest.raises(orch.PartialSaveError) as err:
        save_report(run(DESC, LOC, path), image_name="p.png", image_bytes=PNG, db_path=path)
    assert db.get_complaint(err.value.complaint_id, path) is not None
    assert any(e["step"] == "image_save_failed" for e in db.get_activity(err.value.complaint_id, db_path=path))


def test_overlong_draft_and_blank_draft(path):
    w = run(DESC, LOC, path)
    with pytest.raises(ValueError):
        save_report(w, draft="x" * 7000, db_path=path)
    cid = save_report(w, draft="   ", db_path=path)              # blank edit falls back to the generated draft
    assert db.get_complaint(cid, path)["draft"] == w["analysis"]["draft"]


def test_script_text_is_stored_verbatim_as_text(path):
    w = run("Pothole <script>alert(1)</script> on the main road near the market gate", "Gate <b>5</b>", path)
    cid = save_report(w, db_path=path)
    assert "<script>" in db.get_complaint(cid, path)["description"]   # kept as inert text; the UI never renders it as HTML


def test_control_characters_and_whitespace_are_normalised(path):
    w = run("Garbage\x00 pile\x07 on   the   road near market gate\n\n\n\nsmells bad", "  Market   Gate ", path)
    a = w["analysis"]
    assert "\x00" not in a["description"] and a["location"] == "Market Gate" and "\n\n\n" not in a["description"]


def test_ambiguous_and_unsupported_input_falls_back_to_other_with_low_confidence(path):
    w = run("My neighbour keeps playing loud music late at night", "Elm Street", path)
    assert w["ok"] and w["analysis"]["category"] == "other" and w["analysis"]["confidence_label"] == "low"


def test_unusually_long_input_is_rejected_cleanly(path):
    assert not run("x" * 2500, LOC, path)["ok"]
