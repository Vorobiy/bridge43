"""A small, self-hosted map of Cameroon (no map service needed, works offline).

Schools are placed by their shared WhatsApp location when we have it, otherwise at their town, otherwise at
their region's centre. Regions with no school request yet are the "blind spots" to aim outreach at.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import matching

# Approximate town centres (lat, lon). Good enough to place a dot; a shared location is used when available.
TOWNS = {
    "bamenda": (5.96, 10.15), "bali": (5.89, 10.01), "bambui": (6.02, 10.23), "kumbo": (6.20, 10.68),
    "ndop": (6.00, 10.42), "wum": (6.38, 10.07), "buea": (4.16, 9.24), "limbe": (4.02, 9.21), "kumba": (4.64, 9.45),
    "mamfe": (5.75, 9.31), "douala": (4.05, 9.77), "nkongsamba": (4.95, 9.94), "edea": (3.80, 10.13),
    "yaounde": (3.87, 11.52), "mbalmayo": (3.52, 11.50), "bafia": (4.75, 11.23), "bafoussam": (5.48, 10.42),
    "dschang": (5.45, 10.05), "foumban": (5.73, 10.90), "mbouda": (5.63, 10.25), "bertoua": (4.58, 13.68),
    "batouri": (4.43, 14.36), "abong-mbang": (3.98, 13.18), "ebolowa": (2.91, 11.15), "kribi": (2.94, 9.91),
    "sangmelima": (2.93, 11.98), "ngaoundere": (7.32, 13.58), "meiganga": (6.52, 14.29), "tibati": (6.47, 12.63),
    "garoua": (9.30, 13.40), "guider": (9.93, 13.94), "maroua": (10.59, 14.32), "mokolo": (10.74, 13.80),
    "kousseri": (12.08, 15.03), "mora": (11.05, 14.14), "yagoua": (10.34, 15.23),
}
REGION_CENTRES = {
    "Adamawa": (6.9, 13.3), "Centre": (4.6, 11.8), "East": (4.0, 14.2), "Far North": (11.0, 14.4),
    "Littoral": (4.3, 10.0), "North": (8.6, 13.9), "Northwest": (6.3, 10.3), "South": (2.7, 11.4),
    "Southwest": (5.0, 9.4), "West": (5.4, 10.5),
}
REGIONS = list(REGION_CENTRES)

# Drawing box: Cameroon spans about 8.5-16.2 E and 1.6-13.1 N.
LON0, LON1, LAT0, LAT1 = 8.3, 16.4, 1.5, 13.3
WIDTH = 300
SCALE = WIDTH / (LON1 - LON0)
HEIGHT = round((LAT1 - LAT0) * SCALE)


def xy(lat: float, lon: float) -> tuple[float, float]:
    return round((lon - LON0) * SCALE, 1), round((LAT1 - lat) * SCALE, 1)


@lru_cache(maxsize=1)
def outline_path() -> str:
    data = json.loads((Path(__file__).parent / "static" / "geo" / "cameroon.json").read_text())
    parts = []
    for ring in data["rings"]:
        pts = [xy(lat, lon) for lon, lat in ring]
        parts.append("M" + " L".join(f"{x},{y}" for x, y in pts) + " Z")
    return " ".join(parts)


def _plain(text: str | None) -> str:
    return matching._plain(text or "").replace("é", "e")


def place(data: dict) -> tuple[float, float, str] | None:
    """(lat, lon, how) for a case: exact pin, town, or region centre."""
    loc = data.get("location") or {}
    if loc.get("latitude") is not None and loc.get("longitude") is not None:
        return loc["latitude"], loc["longitude"], "shared location"
    org = data.get("organization") or {}
    town = _plain(org.get("town"))
    for name, (lat, lon) in TOWNS.items():
        if name and name in town:
            return lat, lon, "town centre"
    region = matching.case_region(data)
    if region in REGION_CENTRES:
        lat, lon = REGION_CENTRES[region]
        return lat, lon, "region (approximate)"
    return None


def map_view(cases: list[dict]) -> dict:
    """Dots for every approved school, plus regions with no request yet."""
    dots, unplaced, seen_regions = [], [], {}
    for n, c in enumerate(cases):
        region = matching.case_region(c["data"])
        if region:
            seen_regions[region] = seen_regions.get(region, 0) + 1
        p = place(c["data"])
        if not p:
            unplaced.append(c)
            continue
        x, y = xy(p[0], p[1])
        if p[2] != "shared location":  # spread schools sharing a town or region centre a little
            x += (n % 3 - 1) * 7
            y += (n // 3 % 3 - 1) * 7
        dots.append({"c": c, "x": x, "y": y, "how": p[2]})
    labels = [{"name": r, "x": xy(*REGION_CENTRES[r])[0], "y": xy(*REGION_CENTRES[r])[1],
               "count": seen_regions.get(r, 0)} for r in REGIONS]
    blind = [r for r in REGIONS if not seen_regions.get(r)]
    return {"path": outline_path(), "width": WIDTH, "height": HEIGHT, "dots": dots, "labels": labels,
            "blind_spots": blind, "unplaced": unplaced, "by_region": seen_regions}
