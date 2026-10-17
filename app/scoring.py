"""Priority matrix: a need score and a readiness score (0-100 each) for every approved school case.

Need = how much is missing and how many children it affects. Readiness = whether the site can receive,
use and look after what is delivered. Schools land in one of four boxes (split at 50), or on Safety hold
when the school is closed or not safe to reach.
"""
from __future__ import annotations

LEVEL_POINTS = {"ok": 0, "partial": 1, "critical": 2, "unknown": 0}
# Weight of each profile area in the need score (max raw = 2 x 14 = 28, scaled to 70).
NEED_WEIGHTS = {"learning_space": 3, "water_toilets": 3, "teachers": 2, "materials_results": 2, "barriers": 2,
                "health_wellbeing": 1, "technology": 1}
MAX_RAW = 2 * sum(NEED_WEIGHTS.values())
# Readiness points per item (total 100). "partial" = solar or cuts, calls only, seasonal road...
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


def reach_points(students: int | None) -> int:
    if not students:
        return 0
    return 30 if students >= 600 else 20 if students >= 300 else 10 if students >= 100 else 0


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


def score_case(data: dict) -> dict:
    levels = area_levels(data)
    raw = sum(NEED_WEIGHTS[a] * levels[a] for a in NEED_WEIGHTS)
    students = (data.get("numbers") or {}).get("students")
    reach = reach_points(students)
    need = min(100, round(raw * 70 / MAX_RAW) + reach)

    readiness_block = data.get("readiness") or {}
    readiness, ready_parts, missing_parts = 0, [], []
    for key, (full, part) in READINESS_POINTS.items():
        status = (readiness_block.get(key) or {}).get("status", "unknown")
        pts = full if status == "yes" else part if status == "partial" else 0
        readiness += pts
        (ready_parts if pts else missing_parts).append(READINESS_LABELS[key])

    op = data.get("operating_status") or {}
    hold = op.get("status") == "closed" or op.get("safe_to_reach") == "no"
    if hold:
        quadrant = "safety_hold"
    elif need >= SPLIT:
        quadrant = "act_now" if readiness >= SPLIT else "prepare_first"
    else:
        quadrant = "quick_wins" if readiness >= SPLIT else "monitor"

    drivers = sorted((a for a in NEED_WEIGHTS if levels[a]), key=lambda a: (-levels[a] * NEED_WEIGHTS[a], a))
    org = data.get("organization") or {}
    follow = data.get("_follow_up") or {}
    return {
        "name": org.get("name") or data.get("title") or "Unnamed school",
        "town": org.get("town"), "region": org.get("region"), "students": students,
        "need": need, "readiness": readiness, "reach": reach, "quadrant": quadrant,
        "quadrant_label": QUADRANTS[quadrant][0], "levels": levels,
        "drivers": [a.replace("_", " ") for a in drivers[:3]],
        "ready": ready_parts, "not_ready": missing_parts,
        "urgent_safety": levels.get("safety_operation") == 2,
        "complete": follow.get("complete"), "trust": (data.get("_trust") or {}).get("level", "self_reported"),
        "location": data.get("location") or {},
    }
