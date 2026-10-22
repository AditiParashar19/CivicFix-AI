"""Duplicate detection agent: compares text, location and category with saved reports.

find_duplicates(description, location, category, existing=None, threshold=..., db_path=None) -> DuplicateResult
score = 0.5 * text similarity + 0.3 * location similarity + 0.2 * (same category)
Resolved reports are ignored. Matches only produce a warning: the citizen can still submit.
Borderline pairs (just under the threshold) get an optional LLM semantic check when an API key is set.
"""
import re
from difflib import SequenceMatcher

import config
from models import trace_step
from services import llm

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except ImportError:  # pragma: no cover - fallback path
    _HAS_SKLEARN = False

AGENT = "Duplicate Agent"
_STOP = {"the", "a", "an", "is", "are", "was", "of", "on", "in", "at", "near", "and", "to", "for", "it",
         "there", "this", "that", "has", "have", "been", "with", "from", "by", "very", "please"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def text_similarities(new_text: str, corpus: list[str]) -> tuple[list[float], str]:
    """Similarity of new_text to each corpus text, plus the method name."""
    if not corpus:
        return [], "tfidf" if _HAS_SKLEARN else "token-overlap"
    if _HAS_SKLEARN:
        try:
            m = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform([new_text] + corpus)
            return [float(x) for x in cosine_similarity(m[0], m[1:]).ravel()], "tfidf"
        except ValueError:   # empty vocabulary (e.g. only stop words)
            return [0.0] * len(corpus), "tfidf"
    n = _tokens(new_text)
    return [_jaccard(n, _tokens(c)) for c in corpus], "token-overlap"


def location_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    contain = 0.9 if (ta <= tb or tb <= ta) else 0.0
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(_jaccard(ta, tb), ratio * 0.85, contain), 3)


BORDERLINE_FLOOR = 0.35    # pairs scoring in [floor, threshold) may be re-judged by the LLM
MAX_CANDIDATES = 1000      # most recent unresolved reports compared per check (bounds cost on large datasets)
_REQUIRED = ("complaint_id", "description", "location", "category", "status")
LOCATION_NOTE = "Location similarity compares place names as text; no coordinates are used."


def _load_open_reports(db_path=None) -> list[dict]:
    """Only unresolved reports, newest first, never the whole table."""
    from database import db
    return db.list_open(MAX_CANDIDATES, db_path=db_path)


def _llm_same_issue(description: str, location: str, candidates: list[dict]) -> tuple[set, str]:
    """Ask the LLM which borderline candidates describe the SAME physical issue. Returns (ids, note)."""
    lines = "\n".join(f"- id={c['complaint_id']} | location={llm.wrap(c['location'])} | "
                      f"details={llm.wrap(c['description'][:300])}" for c in candidates)
    prompt = ("Decide which saved reports describe the SAME physical civic issue at the SAME place as the new "
              'report. Be strict: a different street or a different problem is not a duplicate.\n'
              f"New report: location={llm.wrap(location)} | details={llm.wrap(description[:500])}\n"
              f"Saved reports:\n{lines}\n"
              'Return JSON: {"same_issue": [list of ids from above that are the same issue]}')
    obj, note = llm.complete_json(prompt, max_tokens=200, timeout=12)
    if not obj or not isinstance(obj.get("same_issue"), list):
        return set(), note
    valid = {c["complaint_id"] for c in candidates}
    return {str(i) for i in obj["same_issue"] if str(i) in valid}, note


def find_duplicates(description: str, location: str, category: str, existing=None,
                    threshold: float = config.DUPLICATE_THRESHOLD, db_path=None, top_k: int = 3) -> dict:
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    if existing is None:
        existing = _load_open_reports(db_path)
    # tolerate malformed rows instead of crashing the whole workflow
    open_reports = [c for c in existing if isinstance(c, dict) and all(c.get(k) for k in _REQUIRED)
                    and c.get("status") != "Resolved"]
    sims, method = text_similarities(description, [c["description"] for c in open_reports])

    matches, borderline = [], []
    for c, ts in zip(open_reports, sims):
        ls = location_similarity(location, c["location"])
        same_cat = c["category"] == category
        score = round(0.5 * ts + 0.3 * ls + 0.2 * (1.0 if same_cat else 0.0), 3)
        if score >= threshold:
            matches.append({
                "complaint_id": c["complaint_id"], "score": score, "text_similarity": round(ts, 3),
                "location_similarity": ls, "same_category": same_cat, "status": c["status"],
                "category": c["category"], "location": c["location"], "summary": c.get("summary") or "",
                "reasons": [f"text similarity {ts:.2f}", f"location similarity {ls:.2f}",
                            "same category" if same_cat else "different category"]})
        elif score >= BORDERLINE_FLOOR:
            borderline.append((score, ts, ls, same_cat, c))
    llm_note = ""
    if borderline and llm.available():
        borderline.sort(key=lambda b: b[0], reverse=True)
        top = [b[4] for b in borderline[:3]]
        same, llm_note = _llm_same_issue(description, location, top)
        for score, ts, ls, same_cat, c in borderline[:3]:
            if c["complaint_id"] in same:
                matches.append({
                    "complaint_id": c["complaint_id"], "score": max(score, threshold), "text_similarity": round(ts, 3),
                    "location_similarity": ls, "same_category": same_cat, "status": c["status"],
                    "category": c["category"], "location": c["location"], "summary": c.get("summary") or "",
                    "reasons": [f"text similarity {ts:.2f}", f"location similarity {ls:.2f}",
                                "LLM judged this the same issue (borderline score)"]})
    matches.sort(key=lambda m: m["score"], reverse=True)
    matches = matches[:top_k]
    flagged = bool(matches)
    trace = [trace_step(
        AGENT, "compare_with_saved_reports",
        "Possible duplicate found" if flagged else "No duplicate found",
        f"Compared with {len(open_reports)} unresolved reports using {method} text similarity, "
        f"location similarity and category (threshold {threshold}). {LOCATION_NOTE}"
        + (f" LLM re-checked {min(3, len(borderline))} borderline pair(s)." if llm_note else ""),
        f"Closest: {matches[0]['complaint_id']} (score {matches[0]['score']}). User may still submit."
        if flagged else "Report treated as new.")]
    return {"is_potential_duplicate": flagged, "threshold": threshold, "method": method,
            "compared": len(open_reports), "location_method": LOCATION_NOTE,
            "matches": matches, "trace": trace}