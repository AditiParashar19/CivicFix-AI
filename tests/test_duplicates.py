from agents.duplicate_agent import find_duplicates, location_similarity

EXISTING = [
    {"complaint_id": "CF-1", "description": "Large pothole on Sector 4 Main Road near City Market bus stop",
     "category": "pothole", "location": "Sector 4 Main Road, near City Market", "status": "Submitted", "summary": "s"},
    {"complaint_id": "CF-2", "description": "Garbage overflowing outside Greenview Apartments",
     "category": "garbage", "location": "Greenview Apartments, Lake Road", "status": "Submitted", "summary": "s"},
]


def test_similar_report_is_flagged():
    r = find_duplicates("Big pothole on Sector 4 Main Road close to the City Market bus stop",
                        "Main Road, Sector 4, City Market", "pothole", existing=EXISTING)
    assert r["is_potential_duplicate"] and r["matches"][0]["complaint_id"] == "CF-1"
    assert r["trace"][0]["agent"] == "Duplicate Agent"


def test_same_issue_far_away_is_not_flagged():
    r = find_duplicates("Large pothole on the road near the bus stop", "Ring Road petrol pump",
                        "pothole", existing=EXISTING)
    assert not r["is_potential_duplicate"]


def test_different_issue_same_place_is_not_flagged():
    r = find_duplicates("Streetlight is broken and it is dark here", "Sector 4 Main Road, near City Market",
                        "broken streetlight", existing=EXISTING)
    assert not r["is_potential_duplicate"]


def test_resolved_reports_are_ignored():
    resolved = [{**EXISTING[0], "status": "Resolved"}]
    r = find_duplicates(EXISTING[0]["description"], EXISTING[0]["location"], "pothole", existing=resolved)
    assert not r["is_potential_duplicate"]


def test_empty_database():
    r = find_duplicates("Pothole on main road near school", "Main Road", "pothole", existing=[])
    assert r["matches"] == [] and not r["is_potential_duplicate"]


def test_location_similarity_ordering():
    assert location_similarity("Sector 4 Main Road", "Main Road, Sector 4") > 0.8
    assert location_similarity("Sector 4 Main Road", "Temple Street") < 0.3
