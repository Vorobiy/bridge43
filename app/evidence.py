"""Facts worked out from the source items themselves, not by the model.

- trust_level: how well the case is backed up (self-reported < photo-backed < visit-confirmed).
- fill_location: map coordinates from a shared WhatsApp location, a map link or a KoBo GPS field.
"""
from __future__ import annotations

import re

TRUST_LEVELS = ["self_reported", "photo_backed", "visit_confirmed"]
TRUST_LABELS = {"self_reported": "Self-reported", "photo_backed": "Photo-backed", "visit_confirmed": "Visit-confirmed"}

# "Shared location: 4.0511, 9.7679 ..." (written by the Mac app for a WhatsApp location pin), map links, KoBo geopoints.
_PATTERNS = [
    re.compile(r"Shared location:\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", re.I),
    re.compile(r"maps\.(?:google\.[a-z.]+|apple\.com)/\S*?[?&](?:q|ll|query)=(-?\d{1,2}\.\d+)(?:,|%2C)(-?\d{1,3}\.\d+)", re.I),
    re.compile(r"(?:^|\n)\s*(?:gps|geopoint|location|coordinates|position)[\w ]*:\s*(-?\d{1,2}\.\d+)[ ,]+(-?\d{1,3}\.\d+)", re.I),
]


def location_line(lat, lon, label: str | None = None) -> str | None:
    """A WhatsApp location pin as one readable line the case draft can cite and the map can use."""
    try:
        flat, flon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= flat <= 90 and -180 <= flon <= 180):
        return None
    return (f"Shared location: {flat:.6f}, {flon:.6f}" + (f" ({label.strip()})" if label and label.strip() else "")
            + f" https://maps.google.com/?q={flat:.6f},{flon:.6f}")


def _usable(item: dict) -> bool:
    return item.get("status") == "processed" and item.get("direction", "in") != "out"


def trust_level(items: list[dict]) -> dict:
    """visit_confirmed: a field visit form (KoBo) is in the case.
    photo_backed: the contact sent at least one photo or video that was processed.
    self_reported: only messages, calls or notes."""
    used = [i for i in items if _usable(i)]
    forms = [i["id"] for i in used if i.get("channel") == "kobo" and i.get("kind") == "form"]
    photos = [i["id"] for i in used if i.get("kind") in ("image", "video")]
    if forms:
        level, evidence = "visit_confirmed", forms
    elif photos:
        level, evidence = "photo_backed", photos
    else:
        level, evidence = "self_reported", []
    return {"level": level, "label": TRUST_LABELS[level], "photos": len(photos), "visits": len(forms),
            "citations": evidence}


def find_coordinates(text: str | None) -> tuple[float, float] | None:
    if not text:
        return None
    for pat in _PATTERNS:
        m = pat.search(text)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0):
                return lat, lon
    return None


def fill_location(data: dict, items: list[dict]) -> dict:
    """If the model left coordinates empty, take them from the newest item that has them."""
    loc = data.setdefault("location", {"latitude": None, "longitude": None, "description": None, "citations": []})
    if loc.get("latitude") is not None and loc.get("longitude") is not None:
        return data
    for it in sorted((i for i in items if _usable(i)), key=lambda i: i["id"], reverse=True):
        found = find_coordinates(it.get("body")) or find_coordinates(it.get("extracted_text"))
        if found:
            loc["latitude"], loc["longitude"] = found
            loc["citations"] = sorted(set(loc.get("citations") or []) | {it["id"]})
            break
    return data
