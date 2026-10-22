"""Shared constants and configuration: the single source of truth for all modules."""
import os
from pathlib import Path

try:  # python-dotenv is optional; the app works without a .env file
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None or raw.strip() == "" else raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Environment -----------------------------------------------------------------------------
ENVIRONMENT = os.getenv("CIVICFIX_ENV", "development").strip().lower()   # development | production
IS_PRODUCTION = ENVIRONMENT == "production"
OIDC_ROLE_CLAIM = os.getenv("CIVICFIX_OIDC_ROLE_CLAIM", "civicfix_role").strip() or "civicfix_role"

# --- Storage ---------------------------------------------------------------------------------
# If MONGODB_URI is set, MongoDB is used (and a failed connection is an ERROR, never a silent fallback).
# Otherwise a local SQLite file is used (development / offline demo only).
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "civicfix").strip() or "civicfix"
MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "5000"))
DB_PATH = str(_resolve(os.getenv("CIVICFIX_DB_PATH", "data/civicfix.db")))

# --- Demo data (kept separate from real records) ---------------------------------------------
AUTO_SEED = _flag("CIVICFIX_AUTO_SEED", False)                          # never on by default
ENABLE_DEMO_CONTROLS = _flag("CIVICFIX_ENABLE_DEMO_CONTROLS", not IS_PRODUCTION)

UPLOAD_DIR = ROOT / "data" / "uploads"
SAMPLE_DATA_PATH = ROOT / "data" / "sample_complaints.json"

CATEGORIES = ["pothole", "garbage", "broken streetlight", "water leakage", "other"]
STATUSES = ["Submitted", "Under Review", "In Progress", "Resolved"]
PENDING_STATUSES = ["Submitted", "Under Review"]                 # Pending = Submitted + Under Review
OPEN_STATUSES = ["Submitted", "Under Review", "In Progress"]    # not yet resolved (In Progress counted separately)
# Allowed simulated status transitions: one step forward only.
STATUS_TRANSITIONS = {"Submitted": ["Under Review"], "Under Review": ["In Progress"],
                      "In Progress": ["Resolved"], "Resolved": []}
PRIORITIES = ["Low", "Medium", "High", "Critical"]

DESCRIPTION_MIN, DESCRIPTION_MAX = 15, 2000
DRAFT_MAX = 6000
NOTE_MAX = 200
PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX = 25, 200
LOCATION_MIN, LOCATION_MAX = 3, 200

DUPLICATE_THRESHOLD = 0.55          # combined score at/above which we warn
FOLLOWUP_THRESHOLD_DAYS = int(os.getenv("FOLLOWUP_THRESHOLD_DAYS", "3"))

MAX_IMAGE_MB = 5
ALLOWED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"]

# --- LLM (Google Gemini; key via GEMINI_API_KEY or GOOGLE_API_KEY, read in services/llm.py) -------
LLM_MODEL = os.getenv("CIVICFIX_LLM_MODEL", "gemini-2.5-flash")


def label(category: str) -> str:
    """Display label for a category, e.g. 'broken streetlight' -> 'Broken streetlight'."""
    return category.capitalize()