"""Deterministic, explainable classification and priority rules (no API key needed).

classify(text)                   -> ClassificationResult
assess_priority(text, category)  -> PriorityResult  (every point of the score has a stated reason)
"""
import re

import config

# keyword -> weight. Keywords match at a word start, so "leak" also matches "leaking"/"leakage".
CATEGORY_KEYWORDS: dict[str, dict[str, int]] = {
    "pothole": {"pothole": 4, "crater": 3, "sinkhole": 3, "damaged road": 3, "asphalt": 2,
                "tarmac": 2, "road": 1, "crack": 1, "bump": 1},
    "garbage": {"garbage": 4, "trash": 3, "rubbish": 3, "litter": 3, "dustbin": 3, "waste": 2,
                "dump": 2, "bin": 2, "overflowing": 1, "smell": 1, "stench": 1},
    "broken streetlight": {"streetlight": 4, "street light": 4, "lamppost": 3, "lamp": 2, "bulb": 2,
                           "flicker": 2, "light": 1, "dark": 1, "pole": 1},
    "water leakage": {"leak": 3, "pipeline": 3, "burst": 3, "hydrant": 3, "sewage": 2, "waterlogg": 2,
                      "pipe": 2, "flood": 2, "water": 1, "drain": 1, "tap": 1},
}
MIN_SCORE_FOR_CATEGORY = 2

# (points, label, regex). Each group contributes at most once so the score stays explainable.
PRIORITY_RULES = [
    (3, "Safety risk wording",
     r"\b(accident|injur|hurt|fell|slipped|dangerous|danger|hazard|sparking|exposed wir|live wir|"
     r"electrocut|fire|collapse)"),
    (1, "Sensitive or busy location",
     r"\b(school|hospital|clinic|highway|main road|junction|crossing|market|children|child|elderly|"
     r"bus stop|station)"),
    (1, "Long-running problem",
     r"(\bfor (?:\d+|several|many|a few) (?:days|weeks|months)|\bsince\b|\ba week\b|\bweeks\b|"
     r"\bagain\b|\brepeatedly\b|\bno action\b)"),
    (1, "Large scale or health impact",
     r"\b(huge|large|deep|big|major|massive|overflow|burst|flooding|flooded|sewage|contaminat|foul|"
     r"stench|blocked|entire|whole)"),
]
BASE_POINTS = {"pothole": 2, "garbage": 1, "broken streetlight": 2, "water leakage": 2, "other": 1}
PRIORITY_THRESHOLDS = [(6, "Critical"), (4, "High"), (2, "Medium"), (0, "Low")]


def _find(text: str, keyword: str) -> bool:
    return re.search(r"\b" + re.escape(keyword), text) is not None


def classify(text: str) -> dict:
    """Score each category by weighted keyword hits; below the minimum score -> 'other'."""
    t = (text or "").lower()
    scores, matched = {}, []
    for cat, kws in CATEGORY_KEYWORDS.items():
        hits = [k for k in kws if _find(t, k)]
        scores[cat] = sum(kws[k] for k in hits)
        matched += [f"{k} ({cat})" for k in hits]
    best = max(scores, key=lambda c: scores[c])  # ties resolved by CATEGORY_KEYWORDS order
    total = sum(scores.values())
    if scores[best] < MIN_SCORE_FOR_CATEGORY:
        return {"category": "other", "confidence": 0.0 if total == 0 else 0.3,
                "scores": scores, "matched_keywords": matched}
    return {"category": best, "confidence": round(scores[best] / total, 2),
            "scores": scores, "matched_keywords": matched}


def assess_priority(text: str, category: str) -> dict:
    """Rule-based priority. Returns the score and the human-readable reasons behind it."""
    t = (text or "").lower()
    base = BASE_POINTS.get(category, 1)
    score, reasons = base, [f"Base score for '{category}' issues: +{base}"]
    for points, label, pattern in PRIORITY_RULES:
        m = re.search(pattern, t)
        if m:
            score += points
            reasons.append(f"{label} ('{m.group(0).strip()}'): +{points}")
    priority = next(name for floor, name in PRIORITY_THRESHOLDS if score >= floor)
    reasons.append(f"Total score {score} -> {priority} (Critical>=6, High>=4, Medium>=2)")
    return {"priority": priority, "score": score, "reasons": reasons}
