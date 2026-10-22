import pytest

import config
import security
from conftest import REC
from database import db


def test_missing_identity_fails_closed_outside_development():
    assert security.principal_from_claims({}, allow_local_development=False) is None


def test_local_development_has_an_explicit_local_admin():
    principal = security.principal_from_claims({}, allow_local_development=True)
    assert principal.subject == "local-development"
    assert principal.role == "admin"


@pytest.mark.parametrize("claims", [
    {"is_logged_in": True, "sub": "u1"},
    {"is_logged_in": True, "sub": "u1", "email_verified": False, "civicfix_role": "citizen"},
    {"is_logged_in": True, "email": "u@example.org", "civicfix_role": "admin"},
    {"is_logged_in": True, "sub": "u1", "civicfix_role": "superuser"},
])
def test_invalid_or_unassigned_oidc_identity_is_denied(claims):
    assert security.principal_from_claims(claims) is None


def test_verified_oidc_role_claim_resolves_to_principal():
    principal = security.principal_from_claims({
        "is_logged_in": True, "sub": "provider|123", "name": "Citizen",
        "email": "CITIZEN@example.org", "email_verified": True,
        "civicfix_role": ["citizen"],
    })
    assert principal == security.Principal("provider|123", "Citizen", "citizen", "citizen@example.org")


def test_role_transition_policy():
    assert security.role_can_change_status("reviewer", "Submitted", "Under Review")
    assert not security.role_can_change_status("reviewer", "Under Review", "In Progress")
    assert security.role_can_change_status("operator", "Under Review", "In Progress")
    assert not security.role_can_change_status("operator", "In Progress", "Resolved")
    assert not security.role_can_change_status("citizen", "Submitted", "Under Review")


def test_complaint_search_and_details_are_owner_scoped(repo):
    a = db.insert_complaint({**REC, "owner_id": "citizen-a"}, db_path=repo)
    b = db.insert_complaint({**REC, "owner_id": "citizen-b", "location": "Second Street"}, db_path=repo)
    legacy = db.insert_complaint({**REC, "owner_id": None, "location": "Legacy Street"}, db_path=repo)

    mine = db.search_complaints(owner_id="citizen-a", db_path=repo)
    assert [row["complaint_id"] for row in mine["rows"]] == [a]
    assert db.get_complaint(a, db_path=repo, owner_id="citizen-a")["owner_id"] == "citizen-a"
    assert db.get_complaint(b, db_path=repo, owner_id="citizen-a") is None
    assert db.get_complaint(legacy, db_path=repo, owner_id="citizen-a") is None
    assert db.get_stats(db_path=repo, owner_id="citizen-a")["total"] == 1
