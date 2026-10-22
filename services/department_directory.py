"""MOCK department directory. Addresses use the reserved .example domain: nothing here is real,
and nothing in this prototype ever contacts these departments."""
from copy import deepcopy

DEPARTMENTS = {
    "pothole": {"name": "Public Works Department (Roads)", "email": "roads@civic-demo.example",
                "sla_days": 7, "escalation": "Executive Engineer, Roads Division"},
    "garbage": {"name": "Sanitation & Solid Waste Management", "email": "sanitation@civic-demo.example",
                "sla_days": 2, "escalation": "Municipal Health Officer"},
    "broken streetlight": {"name": "Electrical & Street Lighting Department",
                           "email": "streetlights@civic-demo.example",
                           "sla_days": 5, "escalation": "Assistant Engineer, Electrical"},
    "water leakage": {"name": "Water Supply & Sewerage Board", "email": "water@civic-demo.example",
                      "sla_days": 2, "escalation": "Zonal Water Engineer"},
    "other": {"name": "General Grievance Cell", "email": "grievance@civic-demo.example",
              "sla_days": 10, "escalation": "Municipal Commissioner's Office"},
}


def get_department(category: str) -> dict:
    """Department record for a category (falls back to the General Grievance Cell)."""
    key = category if category in DEPARTMENTS else "other"
    return {**deepcopy(DEPARTMENTS[key]), "category": key}


def list_departments() -> list[dict]:
    return [get_department(c) for c in DEPARTMENTS]
