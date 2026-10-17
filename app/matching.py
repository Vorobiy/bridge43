"""Matching donors to school needs ("in common" analysis).

For every open ticket (one school need) and every donor item, check what they have in common:
- type of help: same category, a related one (roof sheets for a classroom build), or money (funds anything);
- place: the donor serves the school's region, or anywhere in Cameroon;
- quantity: how much of the need is still open and how much the donor still has;
- trust: how well PS43 knows the donor.
When no single donor covers a need, several donors are combined (up to 3) until the quantity is covered.
"""
from __future__ import annotations

import re
import unicodedata

# Related kinds of help: a donor of one can usually help with the others in the same group.
CATEGORY_GROUPS = {
    "building": {"classrooms_building", "roofing", "furniture", "safety_security"},
    "water_sanitation": {"water", "sanitation_latrines"},
    "power_tech": {"electricity", "connectivity", "devices"},
    "teaching": {"teacher_training", "staffing", "learning_materials"},
    "access": {"fees_and_access", "birth_registration", "school_meals_health"},
}
CATEGORY_LABELS = {
    "classrooms_building": "classrooms / building", "roofing": "roofing", "water": "water",
    "sanitation_latrines": "toilets / latrines", "furniture": "desks and benches", "electricity": "electricity",
    "connectivity": "internet", "devices": "devices", "learning_materials": "books and materials",
    "teacher_training": "teacher training", "staffing": "teachers", "safety_security": "safety",
    "fees_and_access": "school fees", "birth_registration": "birth certificates",
    "school_meals_health": "meals and health", "funding": "money", "transport": "transport", "other": "other",
}

# Cameroon's 10 regions, with French names and common spellings.
REGION_ALIASES = {
    "adamawa": "Adamawa", "adamaoua": "Adamawa", "centre": "Centre", "center": "Centre", "east": "East",
    "est": "East", "far north": "Far North", "extreme nord": "Far North", "extreme-nord": "Far North",
    "littoral": "Littoral", "north": "North", "nord": "North", "northwest": "Northwest", "north west": "Northwest",
    "nord ouest": "Northwest", "nord-ouest": "Northwest", "south": "South", "sud": "South",
    "southwest": "Southwest", "south west": "Southwest", "sud ouest": "Southwest", "sud-ouest": "Southwest",
    "west": "West", "ouest": "West",
}
# Main towns, so "Bamenda" or "Douala" counts as its region.
TOWN_REGIONS = {
    "bamenda": "Northwest", "bali": "Northwest", "bambui": "Northwest", "kumbo": "Northwest", "ndop": "Northwest",
    "buea": "Southwest", "limbe": "Southwest", "kumba": "Southwest", "mamfe": "Southwest",
    "douala": "Littoral", "nkongsamba": "Littoral", "edea": "Littoral", "yaounde": "Centre", "mbalmayo": "Centre",
    "bafoussam": "West", "dschang": "West", "foumban": "West", "bertoua": "East", "batouri": "East",
    "ebolowa": "South", "kribi": "South", "sangmelima": "South", "ngaoundere": "Adamawa", "meiganga": "Adamawa",
    "garoua": "North", "guider": "North", "maroua": "Far North", "mokolo": "Far North", "kousseri": "Far North",
    "mora": "Far North",
}
NATIONAL_WORDS = ("national", "nationwide", "anywhere", "all regions", "cameroon", "cameroun", "tout le pays",
                  "toutes les regions", "partout")

_NUM = re.compile(r"\d+(?:[ ,. ]\d{3})*(?:\.\d+)?")


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def parse_quantity(text: str | None) -> tuple[float | None, str | None]:
    """'about 60 table-benches' -> (60, 'table-benches'); '500,000 FCFA' -> (500000, 'fcfa')."""
    if not text:
        return None, None
    m = _NUM.search(text)
    if not m:
        return None, None
    raw = m.group(0)
    digits = re.sub(r"[ , ]", "", raw)
    if digits.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", digits):
        digits = digits.replace(".", "")  # 1.000.000 written the French way
    try:
        number = float(digits)
    except ValueError:
        return None, None
    rest = text[m.end():].strip()
    unit = re.match(r"[A-Za-zÀ-ÿ\-]+", rest)
    return number, (_plain(unit.group(0)) if unit else None)


def canon_region(text: str | None) -> str | None:
    """'Nord-Ouest' -> 'Northwest', 'Bamenda' -> 'Northwest', 'anywhere' -> 'National'."""
    if not text:
        return None
    t = _plain(text).replace("region", "").replace("-", " ").strip(" .")
    if any(w in t for w in NATIONAL_WORDS[:3]) or t in ("all", "any", "everywhere"):
        return "National"
    t2 = t.replace(" ", "")
    for alias, region in sorted(REGION_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias.replace(" ", "").replace("-", "") == t2:
            return region
    for town, region in TOWN_REGIONS.items():
        if town in t:
            return region
    for alias, region in sorted(REGION_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(alias)}\b", t):
            return region
    if any(w in t for w in NATIONAL_WORDS):
        return "National"
    return None


def case_region(data: dict) -> str | None:
    org = data.get("organization") or {}
    return canon_region(org.get("region")) or canon_region(org.get("town"))


def region_fit(school_region: str | None, donor_regions: list[str]) -> tuple[int, str] | None:
    """Points and a reason, or None when the donor said they only serve other regions."""
    served = {r for r in (canon_region(x) for x in donor_regions) if r}
    if "National" in served:
        return 20, "serves all of Cameroon"
    if not served or not school_region:
        return 10, "region not confirmed"
    if school_region in served:
        return 25, f"serves {school_region}"
    return None


def relation(need_cat: str, offer_cat: str) -> tuple[int, str] | None:
    if need_cat == offer_cat:
        return 45, f"gives {CATEGORY_LABELS.get(offer_cat, offer_cat)}"
    if offer_cat == "funding":
        return 20, "money can pay for it"
    for group in CATEGORY_GROUPS.values():
        if need_cat in group and offer_cat in group:
            return 25, f"related help ({CATEGORY_LABELS.get(offer_cat, offer_cat)})"
    return None


def _same_unit(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    a, b = a.rstrip("s"), b.rstrip("s")
    return a[:5] == b[:5]


# ---------------------------------------------------------------- trust

def donor_trust(donor: dict, links: list[dict], evidence_level: str = "self_reported") -> dict:
    """0-100. New (<35), Verified (35-69), Trusted (70+). Built from what PS43 has seen, not what donors claim."""
    score, reasons = 10, ["new contact (+10)"]
    if donor.get("verified"):
        score += 30
        reasons.append("identity checked by staff (+30)")
    if donor.get("registered") == "yes":
        score += 5
        reasons.append("says it is a registered organisation (+5)")
    if evidence_level in ("photo_backed", "visit_confirmed"):
        score += 10
        reasons.append("sent photos or documents (+10)")
    done = sum(1 for l in links if l["status"] == "completed")
    delivered = sum(1 for l in links if l["status"] == "delivered")
    cancelled = sum(1 for l in links if l["status"] == "cancelled")
    if done:
        score += min(45, 15 * done)
        reasons.append(f"{done} case{'s' if done > 1 else ''} completed with PS43 (+{min(45, 15 * done)})")
    if delivered:
        score += min(10, 5 * delivered)
        reasons.append(f"{delivered} delivery waiting for a check (+{min(10, 5 * delivered)})")
    if cancelled:
        score -= 15 * cancelled
        reasons.append(f"{cancelled} commitment{'s' if cancelled > 1 else ''} not kept (-{15 * cancelled})")
    score = max(0, min(100, score))
    level = "Trusted" if score >= 70 else "Verified" if score >= 35 else "New"
    return {"score": score, "level": level, "reasons": reasons}


# ---------------------------------------------------------------- matching

def remaining_need(ticket: dict, ticket_links: list[dict]) -> float | None:
    if ticket.get("quantity_total") is None:
        return None
    given = sum(l["quantity"] or 0 for l in ticket_links if l["status"] != "cancelled")
    return max(0.0, ticket["quantity_total"] - given)


def candidates(ticket: dict, school_region: str | None, donors: dict[int, dict], items: list[dict],
               trust: dict[int, dict], ticket_links: list[dict]) -> list[dict]:
    """Every donor item that could help with this ticket, best first."""
    need_left = remaining_need(ticket, ticket_links)
    already = {l["donor_item_id"] for l in ticket_links if l["status"] != "cancelled"}
    out = []
    for it in items:
        donor = donors.get(it["donor_id"])
        if not donor or it["status"] != "available" or it["id"] in already:
            continue
        rel = relation(ticket["category"], it["category"])
        if not rel:
            continue
        fit = region_fit(school_region, donor["regions"])
        if fit is None:
            continue  # the partner only serves other regions
        if it.get("abroad"):
            fit = (min(fit[0], 15), f"abroad ({it.get('location') or 'outside Cameroon'}): needs shipping")
        if it["quantity_total"] is not None:
            available = it["quantity_total"] - (it["committed"] or 0)
            if available <= 0:
                continue
        else:
            available = None
            if it["link_count"]:
                continue  # an item without a number can only be promised once
        # quantities only add up for the same kind of help (400 roof sheets do not cover 60 desks)
        comparable = (rel[0] == 45 and need_left is not None and available is not None
                      and _same_unit(ticket.get("unit"), it.get("unit")))
        if comparable:
            covers = min(available, need_left)
            q_pts, q_reason = (15, "enough to cover it") if available >= need_left else (
                7, f"covers {covers:g} of {need_left:g}")
        else:
            covers, q_pts, q_reason = None, 5, "quantity to confirm"
        t = trust.get(donor["id"], {"score": 10, "level": "New"})
        t_pts = round(t["score"] * 0.15)
        score = min(100, rel[0] + fit[0] + q_pts + t_pts)
        out.append({
            "donor_id": donor["id"], "donor_name": donor["name"], "item_id": it["id"],
            "item": it["description"], "item_category": it["category"], "available": available,
            "unit": it.get("unit"), "covers": covers, "score": score, "trust": t,
            "exact": rel[0] == 45, "comparable": comparable,
            "reasons": [rel[1], fit[1], q_reason, f"trust {t['level'].lower()} ({t['score']})"],
            "conditions": it.get("conditions"), "availability": it.get("availability"),
            "abroad": bool(it.get("abroad")), "location": it.get("location"),
        })
    out.sort(key=lambda c: (-c["score"], c["donor_name"]))
    return out


def plan(ticket: dict, cands: list[dict], ticket_links: list[dict]) -> dict:
    """Best way to cover the need: one donor, a combination of donors, a partial cover, or nothing yet."""
    need_left = remaining_need(ticket, ticket_links)
    active = [l for l in ticket_links if l["status"] != "cancelled"]
    if need_left == 0 or (need_left is None and active):
        # fully promised, or a partner is already on it and the quantity is not counted
        return {"type": "covered", "picks": [], "covered": None, "need_left": need_left}
    if not cands:
        return {"type": "none", "picks": [], "covered": 0, "need_left": need_left}
    exact = [c for c in cands if c["exact"]]
    if not exact:
        # only related help or money: worth a conversation, not a match yet
        firsts = list({c["donor_id"]: c for c in reversed(cands)}.values())[::-1]  # best item per partner
        return {"type": "related", "picks": firsts[:2], "covered": None, "need_left": need_left}
    top = exact[0]
    if need_left is None or not top["comparable"] or (top["covers"] or 0) >= need_left:
        return {"type": "single", "picks": [top], "covered": top["covers"], "need_left": need_left}
    picks, total, used = [], 0.0, set()
    for c in sorted((c for c in cands if c["comparable"]), key=lambda c: (-(c["covers"] or 0), -c["score"])):
        if c["donor_id"] in used:
            continue
        take = min(c["covers"] or 0, need_left - total)  # only ask each partner for what is still missing
        picks.append({**c, "covers": take})
        used.add(c["donor_id"])
        total += take
        if total >= need_left or len(picks) == 3:
            break
    if total >= need_left and len(picks) > 1:
        return {"type": "combined", "picks": picks, "covered": need_left, "need_left": need_left}
    if picks:
        return {"type": "partial", "picks": picks, "covered": total, "need_left": need_left}
    return {"type": "single", "picks": [top], "covered": None, "need_left": need_left}



def shippers(donors: dict[int, dict], items: list[dict], trust: dict[int, dict]) -> list[dict]:
    """Partners who can move goods into Cameroon or to the school, best trusted first."""
    ids = {d["id"] for d in donors.values() if "shipper" in (d.get("roles") or [])}
    ids |= {it["donor_id"] for it in items if it["category"] == "transport" and it["status"] == "available"}
    out = [{"donor_id": i, "donor_name": donors[i]["name"], "trust": trust.get(i, {"score": 10, "level": "New"})}
           for i in ids if i in donors]
    return sorted(out, key=lambda d: -d["trust"]["score"])


# ---------------------------------------------------------------- price check (quotes)

PRICE_TOLERANCE = 0.25  # more than 25% away from the usual price gets flagged


def unit_price(q: dict) -> float | None:
    return q["amount"] / q["quantity"] if q.get("quantity") else None


def price_check(quote: dict, all_quotes: list[dict]) -> dict:
    """Compare a quote with other quotes for the same kind of item (same category and unit), on any ticket."""
    mine = unit_price(quote)
    if mine is None:
        return {"status": "no_quantity", "label": "Add the quantity to compare prices", "median": None}
    others = [unit_price(o) for o in all_quotes
              if o["id"] != quote["id"] and o["category"] == quote["category"] and unit_price(o) is not None
              and _same_unit(o.get("unit"), quote.get("unit"))]
    if not others:
        return {"status": "first", "label": "First quote for this: no comparison yet", "median": None,
                "unit_price": mine}
    others.sort()
    mid = len(others) // 2
    median = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2
    diff = (mine - median) / median if median else 0
    if diff > PRICE_TOLERANCE:
        status, label = "high", f"{diff:.0%} above other quotes: ask why before accepting"
    elif diff < -PRICE_TOLERANCE:
        status, label = "low", f"{-diff:.0%} below other quotes: check quality and what is included"
    else:
        status, label = "fair", "In line with other quotes"
    return {"status": status, "label": label, "median": median, "unit_price": mine, "diff": diff,
            "compared_with": len(others)}
