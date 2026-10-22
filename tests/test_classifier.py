import pytest

from agents.complaint_agent import analyze_complaint
from services.classifier import assess_priority, classify


@pytest.mark.parametrize("text,expected", [
    ("Huge pothole on the road near the market", "pothole"),
    ("Garbage bin overflowing and a bad smell for days", "garbage"),
    ("Streetlight not working, the lane is dark", "broken streetlight"),
    ("Water pipe burst and leaking onto the road", "water leakage"),
    ("Neighbour plays loud music at night", "other"),
    ("", "other"),
])
def test_category(text, expected):
    assert classify(text)["category"] == expected


def test_confidence_and_keywords_reported():
    r = classify("pothole pothole on the road")
    assert 0 < r["confidence"] <= 1 and r["matched_keywords"]


def test_priority_critical_has_reasons():
    r = assess_priority("Deep pothole on the highway near a school, a child fell", "pothole")
    assert r["priority"] == "Critical"
    assert any("Safety" in x for x in r["reasons"])


def test_priority_low_for_minor_garbage():
    assert assess_priority("Dustbin is full at the park", "garbage")["priority"] == "Low"


def test_exposed_wiring_is_a_safety_risk():
    r = assess_priority("Exposed wiring at the base of the lamp pole", "broken streetlight")
    assert r["priority"] in ("High", "Critical")


def test_analysis_respects_user_category_and_is_complete():
    a = analyze_complaint("Large crack and hole in the road near the park", "Park Road",
                          category_hint="water leakage", use_llm=False)
    assert a["category"] == "water leakage" and a["category_source"] == "user"
    assert a["summary"] and "Park Road" in a["draft"] and "DRAFT" in a["draft"]
    assert len(a["trace"]) >= 3


def test_analysis_without_hint_uses_classifier():
    a = analyze_complaint("Garbage overflowing near the bin outside the market", "Market Road", use_llm=False)
    assert a["category"] == "garbage" and a["category_source"] == "classifier"
