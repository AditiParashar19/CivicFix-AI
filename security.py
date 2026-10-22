"""OIDC-backed identity and role policy for the Streamlit application.

Streamlit verifies the OIDC token and exposes its claims through ``st.user``. Role
assignment must be made by the identity provider; CivicFix never trusts a role
from a form, query parameter, or mutable session-state value.
"""
from dataclasses import dataclass
from typing import Any, Optional

import config

ROLES = ("citizen", "reviewer", "operator", "admin")
ROLE_RANK = {role: index for index, role in enumerate(ROLES)}


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    role: str
    email: str = ""

    @property
    def is_staff(self) -> bool:
        return self.role in {"reviewer", "operator", "admin"}


def principal_from_claims(claims: Any, allow_local_development: bool = False) -> Optional[Principal]:
    """Resolve a principal from verified claims, or return None when access is denied."""
    if hasattr(claims, "to_dict"):
        claims = claims.to_dict()
    if not isinstance(claims, dict):
        claims = {}

    if not claims.get("is_logged_in"):
        if allow_local_development:
            return Principal("local-development", "Local developer", "admin")
        return None

    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    if not subject or claims.get("email_verified") is False:
        return None

    raw_roles = claims.get(config.OIDC_ROLE_CLAIM, claims.get("role", ""))
    if isinstance(raw_roles, str):
        raw_roles = [raw_roles]
    if not isinstance(raw_roles, (list, tuple, set)):
        return None
    roles = {str(role).strip().lower() for role in raw_roles}
    allowed = roles.intersection(ROLES)
    if not allowed:
        return None
    role = max(allowed, key=ROLE_RANK.__getitem__)
    name = str(claims.get("name") or claims.get("preferred_username") or email or subject)
    return Principal(subject=subject, display_name=name[:120], role=role, email=email)


def current_principal() -> Optional[Principal]:
    """Read identity afresh for every Streamlit run; development mode is local-only."""
    if not config.IS_PRODUCTION:
        return Principal("local-development", "Local developer", "admin")
    import streamlit as st

    user = getattr(st, "user", {})
    return principal_from_claims(user)


def role_can_change_status(role: str, old_status: str, new_status: str) -> bool:
    """Keep lifecycle writes under explicit staff permissions."""
    if role == "admin":
        return new_status in config.STATUS_TRANSITIONS.get(old_status, [])
    if role == "reviewer":
        return old_status == "Submitted" and new_status == "Under Review"
    if role == "operator":
        return old_status == "Under Review" and new_status == "In Progress"
    return False


def show_login_or_denial() -> None:
    """Fail closed in production when the OIDC identity or role claim is missing."""
    import streamlit as st

    principal = current_principal()
    if principal:
        return
    st.title("CivicFix AI sign in")
    if not config.IS_PRODUCTION:
        st.error("No local development identity is available.")
        st.stop()
    user = getattr(st, "user", {})
    if getattr(user, "is_logged_in", False):
        st.error(f"Your signed-in account has no valid CivicFix role. Ask an administrator to assign one of: {', '.join(ROLES)}.")
        st.stop()
    st.info("Sign in with your organization account. Access is granted only to accounts with a CivicFix role.")
    try:
        configured = bool(st.secrets.get("auth"))
    except Exception:
        configured = False
    if configured:
        st.button("Sign in", on_click=st.login)
    else:
        st.error("Authentication is not configured. Set up the OIDC provider in .streamlit/secrets.toml before deployment.")
    st.stop()


def require_roles(allowed_roles: set[str]) -> Principal:
    """Guard each page as well as the navigation menu, including direct page loads."""
    import streamlit as st

    principal = current_principal()
    if principal is None:
        show_login_or_denial()
        st.stop()
    if principal.role not in allowed_roles:
        st.error("Your account does not have permission to open this page.")
        st.stop()
    return principal
