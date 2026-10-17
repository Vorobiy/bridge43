"""Priority matrix: a need score and a readiness score (0-100 each) for every approved school case.

Need = how much is missing and how many children it affects. Readiness = whether the site can receive,
use and look after what is delivered. Schools land in one of four boxes (split at 50 by default), or on
Safety hold when the school is closed or not safe to reach. Every score comes with its reasons in plain words,
and staff can change the weights in Settings (stored in the database), so the ranking is never a black box.
"""
from __future__ import annotations

LEVEL_POINTS = {"ok": 0, "partial": 1, "critical": 2, "unknown": 0}
LEVEL_WORDS = {1: "partly missing", 2: "critical"}
# Default weight of each profile area in the need score (need = weighted gaps scaled to 70, + up to 30 for pupils).
NEED_WEIGHTS = {"learning_space": 3, "water_toilets": 3, "teachers": 2, "materials_results": 2, "barriers": 2,
                "health_wellbeing": 1, "technology": 1}
AREA_LABELS = {"learning_space": "classrooms and furniture", "water_toilets": "water and toilets",
               "teachers": "teachers", "materials_results": "books and materials", "barriers": "barriers for children",
               "health_wellbeing": "meals and health", "technology": "technology", "safety_operation": "building safety"}
# Default readiness points per item: (full, partial). Totals 100.
READINESS_POINTS = {"electricity": (25, 15), "internet": (20, 10), "secure_storage": (15, 7),
                    "trained_staff": (15, 7), "road_access": (15, 10), "parent_association": (10, 5)}
READINESS_LABELS = {"electricity": "power", "internet": "phone signal", "secure_storage": "secure storage",
                    "trained_staff": "someone to maintain it", "road_access": "truck access",
                    "parent_association": "parents' association"}
# Which profile area a listed need belongs to (used when the profile itself was left unknown).
CATEGORY_AREA = {
    "classrooms_building": "learning_space", "roofing": "learning_space", "furniture": "learning_space",
    "water": "water_toilets", "sanitation_latrines": "water_toilets",
    "teacher_training": "teachers", "staffing": "teachers", "learning_materials": "materials_results",
    "fees_and_access": "barriers", "birth_registration": "barriers", "school_meals_health": "health_wellbeing",
    "electricity": "technology", "connectivity": "technology", "devices": "technology",
    "safety_security": "safety_operation",
}
QUADRANTS = {
    "act_now": ("Act now", "High need, ready to receive help"),
    "prepare_first": ("Prepare first", "High need, but the site is not ready yet"),
    "quick_wins": ("Quick wins", "Smaller needs, ready to receive help"),
    "monitor": ("Monitor", "Smaller needs, not ready yet"),
    "safety_hold": ("Safety hold", "Closed or not safe to reach: check before sending anything"),
}
SPLIT = 50
PUPIL_BANDS = [(600, 30), (300, 20), (100, 10)]
# Vulnerability: extra need points (up to VULNERABILITY_MAX) for children more at risk. Regions where
# insecurity affects schools by default; staff can change the list in Settings.
VULNERABILITY_MAX = 15
CRISIS_REGIONS = ["Far North", "Northwest", "Southwest"]


def defaults() -> dict:
    return {"need_weights": dict(NEED_WEIGHTS),
            "readiness_points": {k: list(v) for k, v in READINESS_POINTS.items()},
            "split": SPLIT, "vulnerability_max": VULNERABILITY_MAX, "crisis_regions": list(CRISIS_REGIONS)}


def config() -> dict:
    """Weights in use: staff changes from Settings on top of the defaults."""
    from . import db
    cfg = defaults()
    try:
        saved = db.get_setting("scoring", {}) or {}
    except Exception:  # database not set up yet (e.g. a quick script): use the defaults
        saved = {}
    for k, v in (saved.get("need_weights") or {}).items():
        if k in cfg["need_weights"]:
            cfg["need_weights"][k] = max(0, int(v))
    for k, v in (saved.get("readiness_points") or {}).items():
        if k in cfg["readiness_points"] and isinstance(v, (list, tuple)) and len(v) == 2:
            cfg["readiness_points"][k] = [max(0, int(v[0])), max(0, int(v[1]))]
    if saved.get("split") is not None:
        cfg["split"] = min(95, max(5, int(saved["split"])))
    if saved.get("vulnerability_max") is not None:
        cfg["vulnerability_max"] = min(40, max(0, int(saved["vulnerability_max"])))
    if isinstance(saved.get("crisis_regions"), list):
        cfg["crisis_regions"] = [str(r) for r in saved["crisis_regions"]]
    return cfg


def vulnerability(data: dict, cfg: dict) -> tuple[int, list[str]]:
    """Extra need points for children more at risk, with the reasons. Only what the sources say is counted."""
    from . import matching
    v = data.get("vulnerability") or {}
    students = (data.get("numbers") or {}).get("students") or 0
    girls = (data.get("numbers") or {}).get("girls")
    raw, why = 0, []
    region = matching.case_region(data)
    if v.get("conflict_affected") == "yes":
        raw += 5
        why.append("affected by conflict")
    elif region and region in cfg["crisis_regions"]:
        raw += 5
        why.append(f"crisis-affected region ({region})")
    displaced = v.get("displaced_pupils") or 0
    if displaced:
        raw += 4 if students and displaced / students >= 0.1 else 2
        why.append(f"{displaced} displaced or refugee pupils")
    if v.get("pupils_with_disability"):
        raw += 2
        why.append(f"{v['pupils_with_disability']} pupils with a disability")
    if v.get("orphans"):
        raw += 2
        why.append(f"{v['orphans']} orphans")
    if girls is not None and students and girls / students < 0.4:
        raw += 2
        why.append(f"few girls enrolled ({round(100 * girls / students)}%)")
    return round(min(raw, 15) * cfg["vulnerability_max"] / 15), why


def reach_points(students: int | None) -> int:
    for floor, pts in PUPIL_BANDS:
        if students and students >= floor:
            return pts
    return 0


def area_levels(data: dict) -> dict[str, int]:
    """0/1/2 per area: the profile rating, raised by any listed need in that area (critical/high urgency = 2)."""
    profile = data.get("profile") or {}
    levels = {k: LEVEL_POINTS.get((profile.get(k) or {}).get("status", "unknown"), 0)
              for k in [*NEED_WEIGHTS, "safety_operation"]}
    for need in data.get("needs") or []:
        area = CATEGORY_AREA.get(need.get("category"))
        if area:
            implied = 2 if need.get("urgency") in ("critical", "high") else 1
            levels[area] = max(levels[area], implied)
    return levels


def score_case(data: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    weights, rpoints, split = cfg["need_weights"], cfg["readiness_points"], cfg["split"]
    levels = area_levels(data)
    max_raw = 2 * sum(weights.values()) or 1
    raw = sum(weights[a] * levels[a] for a in weights)
    students = (data.get("numbers") or {}).get("students")
    reach = reach_points(students)
    vuln, vuln_why = vulnerability(data, cfg)
    need = min(100, round(raw * 70 / max_raw) + reach + vuln)

    readiness_block = data.get("readiness") or {}
    total_ready = sum(v[0] for v in rpoints.values()) or 1
    got, ready_parts, missing_parts = 0, [], []
    for key, (full, part) in rpoints.items():
        status = (readiness_block.get(key) or {}).get("status", "unknown")
        pts = full if status == "yes" else part if status == "partial" else 0
        got += pts
        (ready_parts if pts else missing_parts).append(READINESS_LABELS[key])
    readiness = round(100 * got / total_ready)

    op = data.get("operating_status") or {}
    hold = op.get("status") == "closed" or op.get("safe_to_reach") == "no"
    if hold:
        quadrant = "safety_hold"
    elif need >= split:
        quadrant = "act_now" if readiness >= split else "prepare_first"
    else:
        quadrant = "quick_wins" if readiness >= split else "monitor"

    drivers = sorted((a for a in weights if levels[a] and weights[a]), key=lambda a: (-levels[a] * weights[a], a))
    org = data.get("organization") or {}
    follow = data.get("_follow_up") or {}
    gaps = [f"{AREA_LABELS[a]} {LEVEL_WORDS[levels[a]]}" for a in drivers[:3]]
    why = why_text(quadrant, gaps, students, reach, ready_parts, missing_parts, op, levels, vuln_why, vuln)
    return {
        "name": org.get("name") or data.get("title") or "Unnamed school",
        "town": org.get("town"), "region": org.get("region"), "students": students,
        "need": need, "readiness": readiness, "reach": reach, "vulnerability": vuln, "vulnerable": vuln_why,
        "quadrant": quadrant,
        "quadrant_label": QUADRANTS[quadrant][0], "levels": levels,
        "drivers": [AREA_LABELS[a] for a in drivers[:3]], "gaps": gaps, "why": why,
        "ready": ready_parts, "not_ready": missing_parts,
        "urgent_safety": levels.get("safety_operation") == 2,
        "complete": follow.get("complete"), "trust": (data.get("_trust") or {}).get("level", "self_reported"),
        "location": data.get("location") or {},
    }


def why_text(quadrant, gaps, students, reach, ready, missing, op, levels, vuln_why=(), vuln=0) -> str:
    """One sentence a donor or a judge can read: why this school is in this box."""
    label = QUADRANTS[quadrant][0]
    if quadrant == "safety_hold":
        state = "closed" if op.get("status") == "closed" else "not safe to reach"
        return f"{label}: the school is {state}. Check safety before sending anyone or anything."
    parts = []
    if gaps:
        parts.append(", ".join(gaps))
    if students:
        parts.append(f"{students} pupils" + (f" (+{reach})" if reach else ""))
    if vuln_why:
        parts.append("vulnerable: " + ", ".join(vuln_why) + (f" (+{vuln})" if vuln else ""))
    need_part = "; ".join(parts) or "few gaps reported so far"
    if quadrant in ("act_now", "quick_wins"):
        ready_part = "ready to receive help" + (f" ({', '.join(ready[:3])})" if ready else "")
    else:
        ready_part = "not ready yet: no " + ", no ".join(missing[:3]) if missing else "not ready yet"
    if levels.get("safety_operation") == 2:
        need_part += "; building safety is critical"
    return f"{label} because: {need_part}; {ready_part}."
