"""MongoDB (Atlas) repository. Same contract and query semantics as the SQLite backend.

Collections
  complaints  one document per report; ``status_history`` is an embedded array so a status change and its
              history entry are written in ONE atomic update.
  activity    one document per agent/system event (complaint_id may be null for unattached events).

Credentials come only from the environment (MONGODB_URI). Error messages never include the URI.
pymongo is imported lazily so SQLite-only installs do not need it.
"""
import logging
import re
from typing import Any, Optional

import config
from database import _common as c
from models import utc_now

log = logging.getLogger(__name__)
_NO_ID = {"_id": 0}


def _rx(token: str) -> dict:
    return {"$regex": re.escape(token), "$options": "i"}


def _import_pymongo():
    try:
        import pymongo
        from pymongo import errors
    except ImportError as exc:
        raise c.DatabaseError("MongoDB is configured but 'pymongo' is not installed. "
                              "Run: pip install -r requirements.txt") from exc
    return pymongo, errors


COMPLAINT_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["complaint_id", "description", "category", "location", "priority", "department", "status",
                 "created_at", "updated_at"],
    "properties": {
        "complaint_id": {"bsonType": "string", "pattern": "^CF-[0-9]{8}-[0-9A-F]{4}$"},
        "category": {"enum": config.CATEGORIES}, "priority": {"enum": config.PRIORITIES},
        "status": {"enum": config.STATUSES},
        "description": {"bsonType": "string", "minLength": 1}, "location": {"bsonType": "string", "minLength": 1},
        "department": {"bsonType": "string"}, "created_at": {"bsonType": "string"}, "updated_at": {"bsonType": "string"},
        "status_history": {"bsonType": "array"}, "is_demo": {"bsonType": "bool"},
        "owner_id": {"bsonType": ["string", "null"]}}}}
EVENT_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object", "required": ["timestamp", "agent", "step"],
    "properties": {"timestamp": {"bsonType": "string"}, "agent": {"bsonType": "string"}, "step": {"bsonType": "string"}}}}


class MongoBackend:
    kind = "mongodb"

    def __init__(self, uri: Optional[str] = None, database: Optional[str] = None, client: Any = None):
        """``client`` lets tests inject mongomock; production uses the URI from the environment."""
        self._uri = uri if uri is not None else config.MONGODB_URI
        self._dbname = database or config.MONGODB_DATABASE
        self._client = client
        self._db = None
        self._errors = None

    # ---- connection -------------------------------------------------------------------------
    def _database(self):
        if self._db is not None:
            return self._db
        pymongo, errors = _import_pymongo()
        self._errors = errors
        if self._client is None:
            if not self._uri:
                raise c.DatabaseError("MONGODB_URI is not set.")
            try:
                self._client = pymongo.MongoClient(self._uri, serverSelectionTimeoutMS=config.MONGODB_TIMEOUT_MS,
                                                   appname="civicfix-ai")
            except Exception as exc:   # malformed URI etc.; never echo the URI itself
                raise c.DatabaseError(f"Invalid MongoDB connection settings ({type(exc).__name__}).") from None
        self._db = self._client[self._dbname]
        return self._db

    def _wrap(self, fn):
        """Run a DB call, translating driver errors into DatabaseError without leaking credentials."""
        try:
            return fn()
        except c.DatabaseError:
            raise
        except Exception as exc:
            errors = self._errors
            if errors and isinstance(exc, (errors.ServerSelectionTimeoutError, errors.AutoReconnect)):
                raise c.DatabaseError("Cannot reach MongoDB (timeout). Check the connection string, network "
                                      "access list (IP allow-list) and that the cluster is running.") from None
            if errors and isinstance(exc, errors.OperationFailure):
                if getattr(exc, "code", None) in (13, 18):
                    raise c.DatabaseError("MongoDB rejected the credentials or the user lacks permission.") from None
                msg = (exc.details or {}).get("errmsg") or str(exc) or "unknown error"
                raise c.DatabaseError(f"MongoDB operation failed: {msg[:200]}") from None
            if errors and isinstance(exc, errors.PyMongoError):
                raise c.DatabaseError(f"MongoDB error ({type(exc).__name__}).") from None
            raise

    @property
    def _complaints(self):
        return self._database()["complaints"]

    @property
    def _activity(self):
        return self._database()["activity"]

    def describe(self) -> dict:
        return {"kind": "mongodb", "label": "MongoDB", "persistent": True, "shared": True, "detail": self._dbname}

    def health(self) -> dict:
        def go():
            self._database()
            self._client.admin.command("ping")
            return {"ok": True, **self.describe()}
        return self._wrap(go)

    # ---- schema / lifecycle -----------------------------------------------------------------
    def init(self) -> None:
        def go():
            db = self._database()
            self._client.admin.command("ping")
            existing = set(db.list_collection_names())
            for name, validator in (("complaints", COMPLAINT_VALIDATOR), ("activity", EVENT_VALIDATOR)):
                if name not in existing:
                    try:
                        db.create_collection(name, validator=validator)
                    except self._errors.CollectionInvalid:    # created concurrently
                        pass
                    except NotImplementedError:               # some test doubles lack validators
                        db.create_collection(name)
            cm, ac = self._complaints, self._activity

            def ensure(coll, keys, name, **opts):
                if name not in coll.index_information():
                    coll.create_index(keys, name=name, **opts)

            ensure(cm, "complaint_id", "uniq_complaint_id", unique=True)
            ensure(cm, "workflow_id", "uniq_workflow", unique=True,
                   partialFilterExpression={"workflow_id": {"$type": "string"}})
            ensure(cm, [("status", 1), ("created_at", -1)], "status_created")
            ensure(cm, [("category", 1), ("created_at", -1)], "category_created")
            ensure(cm, [("priority_rank", -1), ("created_at", -1)], "priority_created")
            ensure(cm, [("created_at", -1)], "created")
            ensure(cm, "is_demo", "is_demo")
            ensure(cm, [("owner_id", 1), ("created_at", -1)], "owner_created")
            ensure(ac, [("complaint_id", 1), ("timestamp", 1)], "complaint_time")
            ensure(ac, [("timestamp", -1)], "time")
            ensure(ac, [("agent", 1), ("timestamp", -1)], "agent_time")
            ensure(ac, "dedupe_key", "uniq_dedupe", unique=True,
                   partialFilterExpression={"dedupe_key": {"$type": "string"}})
        self._wrap(go)

    def wipe_all(self) -> None:
        def go():
            self._complaints.delete_many({})
            self._activity.delete_many({})
        self._wrap(go)
        self.init()

    def delete_demo(self) -> int:
        def go():
            ids = [d["complaint_id"] for d in self._complaints.find({"is_demo": True}, {"complaint_id": 1, "_id": 0})]
            if ids:
                self._activity.delete_many({"complaint_id": {"$in": ids}})
                self._complaints.delete_many({"complaint_id": {"$in": ids}})
            return len(ids)
        return self._wrap(go)

    # ---- complaints -------------------------------------------------------------------------
    def insert_complaint(self, record: dict, trace: Optional[list] = None, workflow_id: Optional[str] = None,
                         is_demo: bool = False) -> str:
        doc = c.build_complaint(record, workflow_id, is_demo)
        doc["priority_rank"] = c.PRIORITY_RANK[doc["priority"]]
        if workflow_id is None:
            doc.pop("workflow_id")                      # keep the partial unique index clean
        doc["status_history"] = [{"from_status": None, "to_status": doc["status"], "changed_at": doc["created_at"],
                                  "note": "Fictional sample record loaded" if is_demo else "Report created",
                                  "actor": "Seed" if is_demo else "Citizen (confirmed)"}]

        def go():
            if workflow_id:
                found = self._complaints.find_one({"workflow_id": workflow_id}, {"complaint_id": 1, "_id": 0})
                if found:
                    return found["complaint_id"]
            for _ in range(20):
                cid = c.new_complaint_id()
                try:
                    self._complaints.insert_one({**doc, "complaint_id": cid})
                    break
                except self._errors.DuplicateKeyError:
                    if workflow_id:                      # lost a race with an identical save
                        found = self._complaints.find_one({"workflow_id": workflow_id}, {"complaint_id": 1, "_id": 0})
                        if found:
                            return found["complaint_id"]
                    continue
            else:
                raise c.DatabaseError("Could not allocate a unique complaint ID.")
            try:
                events = [c.build_event(cid, s) for s in trace or []]
                if events:
                    self._activity.insert_many(events, ordered=False)
            except Exception:
                # No multi-document transaction here: compensate so a half-saved report never remains.
                self._complaints.delete_one({"complaint_id": cid})
                self._activity.delete_many({"complaint_id": cid})
                raise
            return cid
        return self._wrap(go)

    @staticmethod
    def _public(doc: Optional[dict]) -> Optional[dict]:
        if doc is None:
            return None
        out = {k: v for k, v in doc.items() if k not in ("_id", "workflow_id", "priority_rank", "status_history")}
        out["is_demo"] = bool(out.get("is_demo"))
        for col in c.COMPLAINT_COLUMNS:
            out.setdefault(col, None)
        return out

    def get_complaint(self, complaint_id: str, owner_id: Optional[str] = None) -> Optional[dict]:
        query = {"complaint_id": complaint_id}
        if owner_id:
            query["owner_id"] = owner_id
        return self._wrap(lambda: self._public(self._complaints.find_one(query, _NO_ID)))

    def get_complaint_id_by_workflow(self, workflow_id: str) -> Optional[str]:
        def go():
            d = self._complaints.find_one({"workflow_id": workflow_id}, {"complaint_id": 1, "_id": 0})
            return d["complaint_id"] if d else None
        return self._wrap(go)

    @staticmethod
    def _query(f: dict) -> dict:
        clauses: list[dict] = []
        for col, key in (("status", "statuses"), ("category", "categories"), ("priority", "priorities")):
            if f[key]:
                clauses.append({col: {"$in": f[key]}})
        rng = {}
        if f["created_from"]:
            rng["$gte"] = f["created_from"]
        if f["created_to_exclusive"]:
            rng["$lt"] = f["created_to_exclusive"]
        if rng:
            clauses.append({"created_at": rng})
        if f.get("owner_id"):
            clauses.append({"owner_id": f["owner_id"]})
        for tok in f["tokens"]:
            clauses.append({"$or": [{fld: _rx(tok)} for fld in c.SEARCH_FIELDS]})
        return {"$and": clauses} if clauses else {}

    _SORTS = {"newest": [("created_at", -1), ("complaint_id", 1)], "oldest": [("created_at", 1), ("complaint_id", 1)],
              "updated": [("updated_at", -1), ("complaint_id", 1)],
              "priority": [("priority_rank", -1), ("created_at", -1), ("complaint_id", 1)]}

    def search_complaints(self, filters: dict, sort: str = "newest", page: int = 1,
                          page_size: int = config.PAGE_SIZE_DEFAULT) -> dict:
        if sort not in self._SORTS:
            raise ValueError(f"Invalid sort: {sort!r}")
        q = self._query(filters)

        def go():
            size, _ = c.page_bounds(page, page_size)
            total = self._complaints.count_documents(q)
            result = c.page_result([], total, page, page_size)
            cur = (self._complaints.find(q, {**_NO_ID, "status_history": 0}).sort(self._SORTS[sort])
                   .skip((result["page"] - 1) * size).limit(size))
            result["rows"] = [self._public(d) for d in cur]
            return result
        return self._wrap(go)

    def count_by(self, field: str, filters: dict) -> dict:
        if field not in c.COUNT_FIELDS:
            raise ValueError(f"Cannot count by {field!r}")
        q = self._query(filters)

        def go():
            pipeline = ([{"$match": q}] if q else []) + [{"$group": {"_id": f"${field}", "n": {"$sum": 1}}}]
            return {d["_id"]: d["n"] for d in self._complaints.aggregate(pipeline)}
        return self._wrap(go)

    def set_image_path(self, complaint_id: str, image_path: str) -> None:
        self._wrap(lambda: self._complaints.update_one({"complaint_id": complaint_id},
                                                       {"$set": {"image_path": image_path}}))

    def delete_complaint(self, complaint_id: str) -> bool:
        def go():
            res = self._complaints.delete_one({"complaint_id": complaint_id})
            self._activity.delete_many({"complaint_id": complaint_id})
            return res.deleted_count > 0
        return self._wrap(go)

    # ---- status -----------------------------------------------------------------------------
    def update_status(self, complaint_id: str, new_status: str, note: str, actor: str,
                      expected_status: Optional[str] = None) -> bool:
        note = c.clean_note(note)

        def go():
            cur = self._complaints.find_one({"complaint_id": complaint_id}, {"status": 1, "_id": 0})
            if cur is None:
                return False
            old = cur["status"]
            if expected_status and expected_status != old:
                raise c.ConflictError(f"The status changed to '{old}' since this page was loaded. Reload and retry.")
            c.check_transition(old, new_status)
            now = utc_now()
            entry = {"from_status": old, "to_status": new_status, "note": note, "actor": actor, "changed_at": now}
            # compare-and-set: only applies if nobody changed the status in the meantime
            res = self._complaints.update_one({"complaint_id": complaint_id, "status": old},
                                              {"$set": {"status": new_status, "updated_at": now},
                                               "$push": {"status_history": entry}})
            if res.modified_count != 1:
                raise c.ConflictError("The report was changed by someone else. Reload and retry.")
            try:
                self._activity.insert_one(c.build_event(complaint_id, {
                    "timestamp": now, "agent": "Status Update", "step": "status_change",
                    "decision": f"{old} -> {new_status}",
                    "reason": note or "Status changed manually in the prototype UI",
                    "outcome": f"Recorded by {actor}. Simulated change; no real department was contacted."}))
            except Exception:
                # keep status and audit trail consistent: roll the status back and report the failure
                self._complaints.update_one({"complaint_id": complaint_id, "status": new_status},
                                            {"$set": {"status": old, "updated_at": cur.get("updated_at", now)},
                                             "$pop": {"status_history": 1}})
                raise
            return True
        return self._wrap(go)

    def get_status_history(self, complaint_id: str) -> list[dict]:
        def go():
            d = self._complaints.find_one({"complaint_id": complaint_id}, {"status_history": 1, "_id": 0})
            return sorted((d or {}).get("status_history", []), key=lambda e: e["changed_at"])
        return self._wrap(go)

    # ---- activity ---------------------------------------------------------------------------
    def log_activity(self, complaint_id: Optional[str], event: dict) -> bool:
        ev = c.build_event(complaint_id, event)

        def go():
            try:
                self._activity.insert_one(ev)
                return True
            except self._errors.DuplicateKeyError:
                return False
        return self._wrap(go)

    @staticmethod
    def _activity_query(f: dict) -> dict:
        clauses: list[dict] = []
        if f["complaint_id"]:
            clauses.append({"complaint_id": f["complaint_id"]})
        if f["complaint_id_contains"]:
            clauses.append({"complaint_id": _rx(f["complaint_id_contains"])})
        if f["agents"]:
            clauses.append({"agent": {"$in": f["agents"]}})
        if f["steps"]:
            clauses.append({"step": {"$in": f["steps"]}})
        rng = {}
        if f["date_from"]:
            rng["$gte"] = f["date_from"]
        if f["date_to_exclusive"]:
            rng["$lt"] = f["date_to_exclusive"]
        if rng:
            clauses.append({"timestamp": rng})
        for tok in f["outcome_tokens"]:
            clauses.append({"outcome": _rx(tok)})
        for tok in f["text_tokens"]:
            clauses.append({"$or": [{fld: _rx(tok)} for fld in c.ACTIVITY_TEXT_FIELDS]})
        return {"$and": clauses} if clauses else {}

    def get_activity(self, filters: dict, newest_first: bool, limit: int, offset: int) -> list[dict]:
        direction = -1 if newest_first else 1

        def go():
            cur = (self._activity.find(self._activity_query(filters))
                   .sort([("timestamp", direction), ("_id", direction)]).skip(offset).limit(limit))
            out = []
            for i, d in enumerate(cur):
                out.append({"id": str(d.get("_id")), "complaint_id": d.get("complaint_id"),
                            **{k: d.get(k, "") for k in ("timestamp", "agent", "step", "decision", "reason", "outcome")}})
            return out
        return self._wrap(go)

    def count_activity(self, filters: dict) -> int:
        return self._wrap(lambda: self._activity.count_documents(self._activity_query(filters)))

    def distinct_activity(self, field: str) -> list[str]:
        if field not in ("agent", "step"):
            raise ValueError(f"Cannot list values of {field!r}")
        return self._wrap(lambda: sorted(self._activity.distinct(field)))
