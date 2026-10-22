import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # lets tests import stub_streamlit

from database import db  # noqa: E402

REC = {"description": "Large pothole on Main Road near the City Market", "category": "pothole",
       "location": "Main Road, City Market", "priority": "High",
       "department": "Public Works Department (Roads)", "summary": "Pothole on Main Road"}


@pytest.fixture(params=["sqlite", "mongo"])
def repo(request, tmp_path):
    """A clean, isolated storage key usable as ``db_path=`` for every db.* function.

    sqlite -> temp file. mongo -> mongomock if installed, or a real server when CIVICFIX_TEST_MONGODB_URI is
    set (a throw-away database is used and dropped). Skipped, never faked, when neither is available.
    """
    if request.param == "sqlite":
        key = str(tmp_path / "repo.db")
        db.init_db(key)
        return key
    from database.mongo_backend import MongoBackend
    uri = os.getenv("CIVICFIX_TEST_MONGODB_URI")
    if uri:
        pytest.importorskip("pymongo")
        backend = MongoBackend(uri=uri, database=f"civicfix_test_{uuid.uuid4().hex[:8]}")
    else:
        mongomock = pytest.importorskip("mongomock")
        pytest.importorskip("pymongo")
        backend = MongoBackend(uri="mongomock://", database="civicfix_test", client=mongomock.MongoClient())
    key = f"mongo-test-{uuid.uuid4().hex[:6]}"
    db.set_backend(backend, db_path=key)
    backend.init()
    return key


def advance(path, cid, to, note=""):
    """Walk a complaint forward one step at a time until it reaches ``to``."""
    import config
    seq = config.STATUSES
    cur = db.get_complaint(cid, path)["status"]
    for nxt in seq[seq.index(cur) + 1: seq.index(to) + 1]:
        db.update_status(cid, nxt, note, db_path=path)
