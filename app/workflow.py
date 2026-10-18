"""Part two: what happens after staff approve a case.

School need approved  -> one ticket per need, with deployment steps for that kind of help.
Donor/partner offer approved -> the partner is added to (or updated in) the partners database, with its items.
Tickets are matched with one or several partners, messages are logged on the ticket, quotes are price-checked,
and once delivered the school gets follow-up questions at 30, 90 and 180 days. A partner's trust only rises
when the school confirms the work was done.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import db, evidence, gaps, matching, scoring

STATUSES = ["new", "matched", "in_progress", "delivered", "checked", "cancelled"]
STATUS_LABELS = {"new": "New", "matched": "Partner found", "in_progress": "In progress", "delivered": "Delivered",
                 "checked": "Confirmed by school", "cancelled": "Cancelled"}
OPEN_STATUSES = ("new", "matched")
CONFIRM_PREFIX = "School confirms"

STEP_TEMPLATES = {
    "building": [("Site visit: measure and photograph", "partner"), ("Get 2 quotes and check prices", "ps43"),
                 ("Materials bought and delivered", "partner"), ("Work done", "partner"),
                 ("School confirms with photos", "school")],
    "furniture": [("Confirm number and size with the school", "ps43"), ("Get 2 quotes and check prices", "ps43"),
                  ("Made or bought", "partner"), ("Delivered to the school", "partner"),
                  ("School confirms with photos", "school")],
    "water_sanitation": [("Site survey: water source or latrine site", "partner"),
                         ("Get 2 quotes and check prices", "ps43"), ("Build or drill", "partner"),
                         ("Water test or inspection", "partner"), ("Hand over to a school maintenance committee", "school"),
                         ("School confirms with photos", "school")],
    "power_tech": [("Check power, signal and a lockable room", "partner"), ("Get quotes and check prices", "ps43"),
                   ("Deliver the equipment", "partner"), ("Install and set up", "partner"),
                   ("Train 2 staff to look after it", "partner"), ("School confirms with photos", "school")],
    "teaching": [("Agree the plan with the head teacher", "ps43"), ("Deliver training or materials", "partner"),
                 ("School confirms with photos", "school"), ("Check use after 1 month", "ps43")],
    "access": [("List the pupils concerned", "school"), ("Arrange with the partner (civil registry, supplier...)", "ps43"),
               ("Delivered", "partner"), ("School confirms", "school")],
    "default": [("Agree the plan", "ps43"), ("Deliver", "partner"), ("School confirms with photos", "school")],
}
SHIPPING_STEPS = [("Pack and ship from abroad", "partner"), ("Customs clearance in Cameroon", "partner"),
                  ("Transport to the school", "partner")]


def steps_for(category: str) -> list[tuple[str, str]]:
    if category == "furniture":
        return STEP_TEMPLATES["furniture"]
    for name, group in matching.CATEGORY_GROUPS.items():
        if category in group:
            return STEP_TEMPLATES[name]
    return STEP_TEMPLATES["default"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "need"


# ---------------------------------------------------------------- approval hook

def on_case_approved(thread_id: int, who: str = "staff") -> dict:
    """Called when staff approve a case. Returns what was created, for the confirmation message."""
    s = db.latest_approved_summary(thread_id)
    if not s:
        return {}
    data = s["data"]
    bank = gaps.bank_for(data)
    if bank == "donor":
        donor = save_partner(thread_id, data)
        return {"partner": donor}
    if bank == "school" and data.get("case_type") in ("need", "update", "unclear"):
        return {"tickets": create_tickets(thread_id, data, who)}
    return {}


def create_tickets(thread_id: int, data: dict, who: str = "staff") -> list[dict]:
    made = []
    for need in data.get("needs") or []:
        if not (need.get("description") or "").strip():
            continue
        qty, unit = matching.parse_quantity(need.get("quantity"))
        ticket, created = db.upsert_ticket(
            thread_id, _key(need), category=need["category"],
            description=need["description"].strip(), quantity_text=need.get("quantity"), quantity_total=qty,
            unit=unit, urgency=need.get("urgency") or "unknown")
        if created:
            db.add_steps(ticket["id"], steps_for(need["category"]))
            db.add_event(ticket["id"], "created", "Ticket opened from the approved case.", who)
            made.append(ticket)
    return made


def partner_roles(data: dict) -> list[str]:
    donor = data.get("donor") or {}
    gives = set(donor.get("gives") or [])
    org_type = (data.get("organization") or {}).get("type")
    cats = {o.get("category") for o in data.get("offers") or []}
    roles = []
    if gives & {"money", "goods"} or "funding" in cats or org_type == "donor":
        roles.append("donor")
    if gives & {"services", "expertise", "volunteers"} or org_type in ("contractor", "ngo"):
        roles.append("implementer")
    if org_type == "supplier":
        roles.append("supplier")
    if "transport" in gives or "transport" in cats:
        roles.append("shipper")
    return roles or ["donor"]


def save_partner(thread_id: int, data: dict) -> dict:
    thread = db.get_thread(thread_id)
    contact = db.get_contact(thread["contact_id"])
    org, donor = data.get("organization") or {}, data.get("donor") or {}
    regions = donor.get("regions") or [r for r in (org.get("region"), org.get("town")) if r]
    record = db.upsert_donor(
        contact["id"], thread_id, name=org.get("name") or contact.get("display_name") or contact["address"],
        kind=donor.get("kind") or "unknown", gives=donor.get("gives") or [], regions=regions,
        summary=donor.get("provides_summary") or (data.get("situation") or {}).get("text"),
        amount=donor.get("amount"), timeframe=donor.get("timeframe"), conditions=donor.get("conditions"),
        registered=donor.get("registered") or "unknown", past_work_note=donor.get("past_work_with_ps43"),
        roles=partner_roles(data))
    items = []
    for o in data.get("offers") or []:
        if not (o.get("description") or "").strip():
            continue
        qty, unit = matching.parse_quantity(o.get("quantity"))
        if o.get("category") == "funding" and qty is None:
            qty, unit = matching.parse_quantity(donor.get("amount"))
        items.append({"category": o.get("category") or "other", "description": o["description"].strip(),
                      "quantity_text": o.get("quantity"), "quantity_total": qty, "unit": unit,
                      "availability": o.get("availability"), "conditions": o.get("conditions"),
                      "location": o.get("location"), "abroad": bool(o.get("abroad"))})
    db.replace_donor_items(record["id"], thread_id, items)
    return record


# ---------------------------------------------------------------- partners and trust

def trust_map(donors: list[dict] | None = None) -> dict[int, dict]:
    donors = donors if donors is not None else db.list_donors()
    links = db.list_links()
    out = {}
    for d in donors:
        level = "self_reported"
        if d.get("thread_id"):
            items = db.thread_items(d["thread_id"])
            level = evidence.trust_level(items)["level"]
            if any(i["kind"] == "document" and i["direction"] == "in" for i in items) and level == "self_reported":
                level = "photo_backed"
        out[d["id"]] = matching.donor_trust(d, [l for l in links if l["donor_id"] == d["id"]], level)
    return out


def partners_view() -> list[dict]:
    donors = db.list_donors()
    trust = trust_map(donors)
    items = db.list_donor_items()
    links = db.list_links()
    out = []
    for d in donors:
        mine = [i for i in items if i["donor_id"] == d["id"]]
        history = [l for l in links if l["donor_id"] == d["id"]]
        out.append({**d, "trust": trust[d["id"]], "stock": mine, "history": history,
                    "open_items": sum(1 for i in mine if i["status"] == "available"),
                    "completed": sum(1 for l in history if l["status"] == "completed")})
    out.sort(key=lambda d: (-d["trust"]["score"], d["name"].lower()))
    return out


# ---------------------------------------------------------------- schools and the matrix

def _key(need: dict) -> str:
    return f"{need.get('category')}:{_slug(need.get('description') or '')}"


def approved_cases(include_drafts: bool = False) -> list[dict]:
    """Every school case with an approved record (latest approved version).

    include_drafts=True (the Schools tab): score each school from its newest draft instead, so the ranking moves
    as soon as new messages change the picture, and schools still waiting for review are ranked too.
    c["pending"] is then "new" (never approved) or "update" (newer draft than the approved one), else None.
    Needs already delivered are left out of the need score, so a school moves down the matrix once helped.
    """
    done = {(k["thread_id"], k["need_key"]) for k in db.list_tickets() if k["status"] in ("delivered", "checked")}
    cfg = scoring.config()
    out = []
    for t in db.list_threads():
        if t["status"] in ("rejected", "not_relevant", "closed"):
            continue
        s = db.latest_approved_summary(t["id"])
        pending = None
        if include_drafts:
            latest = db.latest_summary(t["id"])
            if latest and latest["status"] == "draft" and (not s or latest["version"] > s["version"]):
                pending = "update" if s else "new"
                s = latest
        if not s or gaps.bank_for(s["data"]) != "school":
            continue
        data = s["data"]
        helped = [n for n in data.get("needs") or [] if (t["id"], _key(n)) in done]
        scored = {**data, "needs": [n for n in data.get("needs") or [] if n not in helped]}
        out.append({"thread": t, "summary": s, "data": data, "score": scoring.score_case(scored, cfg),
                    "helped": [n["description"] for n in helped], "pending": pending})
    return out


def school_names() -> dict[int, str]:
    out = {}
    for t in db.list_threads():
        s = db.latest_approved_summary(t["id"])
        if s:
            out[t["id"]] = (s["data"].get("organization") or {}).get("name") or t["title"]
    return out


def matrix_view() -> dict:
    cases = [c for c in approved_cases(include_drafts=True) if c["thread"]["status"] != "resolved"]
    tickets = db.list_tickets()
    for c in cases:
        mine = [k for k in tickets if k["thread_id"] == c["thread"]["id"]]
        c["tickets"] = mine
        c["open_tickets"] = sum(1 for k in mine if k["status"] not in ("checked", "cancelled"))
    rank = {q: n for n, q in enumerate(["act_now", "prepare_first", "quick_wins", "monitor", "safety_hold"])}
    cases.sort(key=lambda c: (rank[c["score"]["quadrant"]], -c["score"]["need"], -c["score"]["readiness"]))
    boxes = {q: [c for c in cases if c["score"]["quadrant"] == q] for q in scoring.QUADRANTS}
    return {"cases": cases, "boxes": boxes, "quadrants": scoring.QUADRANTS}


# ---------------------------------------------------------------- matching views

def _context():
    donors = {d["id"]: d for d in db.list_donors()}
    trust = trust_map(list(donors.values()))
    items = db.list_donor_items()
    links = db.list_links()
    regions = {c["thread"]["id"]: matching.case_region(c["data"]) for c in approved_cases()}
    return donors, trust, items, links, regions


def ticket_matches(ticket: dict, ctx=None) -> dict:
    donors, trust, items, links, regions = ctx or _context()
    t_links = [l for l in links if l["ticket_id"] == ticket["id"]]
    region = regions.get(ticket["thread_id"])
    if region is None:
        s = db.latest_approved_summary(ticket["thread_id"])
        region = matching.case_region(s["data"]) if s else None
    cands = matching.candidates(ticket, region, donors, items, trust, t_links)
    p = matching.plan(ticket, cands, t_links)
    p["needs_shipping"] = any(c["abroad"] for c in p["picks"])
    p["shippers"] = matching.shippers(donors, items, trust) if p["needs_shipping"] else []
    return {"candidates": cands, "plan": p, "region": region, "links": t_links}


def case_matches(thread_id: int) -> list[dict]:
    """For the case page: each open need of this school with the best partners for it, ready to accept in one click."""
    tickets = [k for k in db.list_tickets(thread_id=thread_id) if k["status"] in ("new", "matched")]
    if not tickets:
        return []
    ctx = _context()
    out = []
    for k in tickets:
        m = ticket_matches(k, ctx)
        p = m["plan"]
        if p["type"] == "covered":
            continue
        out.append({"ticket": k, "plan": p, "picks": p["picks"], "others": max(0, len(m["candidates"]) - len(p["picks"])),
                    "exact": p["type"] in ("single", "combined", "partial")})
    order = {"single": 0, "combined": 0, "partial": 1, "related": 2, "none": 3}
    out.sort(key=lambda r: (order.get(r["plan"]["type"], 9), r["ticket"]["urgency"] != "critical"))
    return out


def case_needs(thread_id: int) -> list[dict]:
    """Every need of a case with its progress and the one next action, for the case page."""
    out = []
    links = db.list_links()
    for k in db.list_tickets(thread_id=thread_id):
        steps = db.list_steps(k["id"])
        mine = [l for l in links if l["ticket_id"] == k["id"] and l["status"] != "cancelled"]
        plan = {"type": "covered", "picks": []} if k["status"] != "new" else ticket_matches(k)["plan"]
        nxt = ticket_next_step(k, plan, steps) if k["status"] not in ("checked", "cancelled") else None
        out.append({"ticket": k, "done": sum(1 for x in steps if x["done_at"]), "total": len(steps),
                    "partners": sorted({l["donor_name"] for l in mine}), "next": nxt})
    order = {"delivered": 0, "in_progress": 1, "matched": 2, "new": 3, "checked": 4, "cancelled": 5}
    return sorted(out, key=lambda r: order.get(r["ticket"]["status"], 9))


def case_history(thread_id: int, limit: int = 30) -> list[dict]:
    """What happened on this case, newest first: drafts, approval, matches, steps, messages, resolution."""
    thread = db.get_thread(thread_id)
    if not thread:
        return []
    ev = [{"at": thread["opened_at"], "who": None, "kind": "start", "text": "First message: case opened."}]
    with db.tx() as conn:
        for s in conn.execute("SELECT version, status, reviewer, created_at, reviewed_at FROM summaries "
                              "WHERE thread_id = ? ORDER BY version", (thread_id,)):
            ev.append({"at": s["created_at"], "who": "AI", "kind": "draft",
                       "text": "AI draft written." if s["version"] == 1 else f"AI draft updated from new messages (v{s['version']})."})
            if s["status"] == "approved" and s["reviewed_at"]:
                ev.append({"at": s["reviewed_at"], "who": s["reviewer"], "kind": "approved", "text": "Draft approved."})
            elif s["status"] == "rejected" and s["reviewed_at"]:
                ev.append({"at": s["reviewed_at"], "who": s["reviewer"], "kind": "rejected", "text": "Draft rejected."})
        for i in conn.execute("SELECT sent_at, author, body FROM items WHERE thread_id = ? AND direction = 'out' "
                              "AND body LIKE '[%'", (thread_id,)):
            label = (i["body"] or "")[1:].split("]", 1)[0]
            ev.append({"at": i["sent_at"], "who": i["author"], "kind": "message", "text": label + "."})
    for k in db.list_tickets(thread_id=thread_id):
        label = matching.CATEGORY_LABELS.get(k["category"], k["category"]).capitalize()
        for e in db.list_events(k["id"]):
            if e["kind"] == "created":
                continue
            if e["kind"] == "status" and (e["text"].startswith("Status:") or e["text"].startswith("Every need")):
                continue  # the step or match that caused it is already listed
            ev.append({"at": e["at"], "who": e["who"], "kind": e["kind"], "text": f"{label}: {e['text']}",
                       "ticket": k["id"], "seq": e["id"]})
    if thread.get("resolved_at"):
        ev.append({"at": thread["resolved_at"], "who": None, "kind": "resolved", "text": "Case resolved: every need confirmed.", "seq": 10**9})
    ev.sort(key=lambda e: (e["at"] or "", e.get("seq", 0), e["kind"] == "resolved"), reverse=True)
    return ev[:limit]


def resolved_cases() -> list[dict]:
    """The results log for Impact: every resolved case with what was delivered, by whom, for how many pupils."""
    cases = {c["thread"]["id"]: c for c in approved_cases()}
    links = db.list_links()
    out = []
    for t in db.list_threads("resolved"):
        tickets = [k for k in db.list_tickets(thread_id=t["id"]) if k["status"] == "checked"]
        c = cases.get(t["id"])
        days = None
        if t.get("resolved_at") and t.get("opened_at"):
            days = max(0, (datetime.fromisoformat(t["resolved_at"]) - datetime.fromisoformat(t["opened_at"])).days)
        out.append({
            "thread": t, "school": (c["score"]["name"] if c else None) or t["title"] or t["display_name"],
            "region": c["score"]["region"] if c else None, "pupils": c["score"]["students"] if c else None,
            "delivered": [matching.CATEGORY_LABELS.get(k["category"], k["category"]) + (f" ({k['quantity_text']})" if k.get("quantity_text") else "")
                          for k in tickets],
            "partners": sorted({l["donor_name"] for l in links if l["ticket_id"] in {k["id"] for k in tickets}
                                and l["status"] != "cancelled"}),
            "days": days, "resolved_at": t.get("resolved_at")})
    out.sort(key=lambda r: r["resolved_at"] or "", reverse=True)
    return out


def coverage_view() -> dict:
    """The 'in common' analysis: every open need against every partner offer."""
    ctx = _context()
    donors, trust, items, links, regions = ctx
    rows = []
    names = school_names()
    for k in db.list_tickets():
        if k["status"] not in OPEN_STATUSES:
            continue
        k["school"] = names.get(k["thread_id"]) or k["display_name"]
        m = ticket_matches(k, ctx)
        rows.append({"ticket": k, **m})
    order = {"none": 0, "related": 1, "partial": 2, "single": 3, "combined": 4, "covered": 5}
    rows.sort(key=lambda r: (order[r["plan"]["type"]], r["ticket"]["urgency"] != "critical"))
    cats = sorted({r["ticket"]["category"] for r in rows} | {i["category"] for i in items if i["status"] == "available"})
    by_cat = []
    for c in cats:
        need_rows = [r for r in rows if r["ticket"]["category"] == c]
        offers = [i for i in items if i["category"] == c and i["status"] == "available"]
        by_cat.append({"category": c, "label": matching.CATEGORY_LABELS.get(c, c), "needs": len(need_rows),
                       "offers": len(offers), "partners": len({i["donor_id"] for i in offers}),
                       "matched": sum(1 for r in need_rows if r["plan"]["type"] in ("single", "combined", "covered"))})
    counts = {t: sum(1 for r in rows if r["plan"]["type"] == t) for t in order}
    return {"rows": rows, "by_category": by_cat, "counts": counts}


def partner_can_help(donor_id: int) -> list[dict]:
    ctx = _context()
    out = []
    names = school_names()
    for k in db.list_tickets():
        if k["status"] not in OPEN_STATUSES:
            continue
        k["school"] = names.get(k["thread_id"]) or k["display_name"]
        m = ticket_matches(k, ctx)
        mine = [c for c in m["candidates"] if c["donor_id"] == donor_id]
        if mine:
            out.append({"ticket": k, "best": mine[0]})
    return sorted(out, key=lambda r: -r["best"]["score"])


# ---------------------------------------------------------------- ticket actions

def assign(ticket_id: int, donor_id: int, item_id: int | None, quantity: float | None, who: str) -> None:
    ticket = db.get_ticket(ticket_id)
    donor = db.get_donor(donor_id)
    item = next((i for i in db.list_donor_items(donor_id) if i["id"] == item_id), None) if item_id else None
    db.add_link(ticket_id, donor_id, item_id, quantity)
    if ticket["status"] == "new":
        db.set_ticket(ticket_id, status="matched")
    what = f"{quantity:g} × {item['description']}" if (quantity and item) else (item["description"] if item else "help")
    db.add_event(ticket_id, "match", f"Matched with {donor['name']}: {what}.", who)
    if item and item.get("abroad"):
        steps = db.list_steps(ticket_id)
        missing = [s for s in SHIPPING_STEPS if s[0] not in {x["title"] for x in steps}]
        # shipping comes before the delivery/installation steps
        where = next((s["position"] for s in steps if not s["done_at"] and s["owner"] != "ps43"
                      and re.match(r"(Deliver|Install|Build|Materials|Made|Transport)", s["title"])),
                     next((s["position"] for s in steps if s["title"].startswith(CONFIRM_PREFIX)), len(steps) + 1))
        if missing:
            db.insert_steps(ticket_id, where, missing)
        db.add_event(ticket_id, "note", f"Items are abroad ({item.get('location') or 'outside Cameroon'}): "
                                        "shipping steps added.", who)


def cancel_link(ticket_id: int, link_id: int, who: str) -> None:
    link = next((l for l in db.list_links(ticket_id=ticket_id) if l["id"] == link_id), None)
    if not link:
        return
    db.set_link(link_id, status="cancelled")
    db.add_event(ticket_id, "match", f"{link['donor_name']} can no longer help (commitment cancelled).", who)
    if not [l for l in db.list_links(ticket_id=ticket_id) if l["status"] != "cancelled"]:
        ticket = db.get_ticket(ticket_id)
        if ticket["status"] == "matched":
            db.set_ticket(ticket_id, status="new")


def set_status(ticket_id: int, status: str, who: str, note: str | None = None) -> None:
    ticket = db.get_ticket(ticket_id)
    if status not in STATUSES or ticket["status"] == status:
        return
    fields = {"status": status}
    if status == "delivered":
        fields["delivered_at"] = db.now()
        db.add_followups(ticket_id, datetime.now(timezone.utc))
        db.set_links_for_ticket(ticket_id, "delivered", ("committed",))
    elif status == "checked":
        if not ticket.get("delivered_at"):
            fields["delivered_at"] = db.now()
            db.add_followups(ticket_id, datetime.now(timezone.utc))
        db.set_links_for_ticket(ticket_id, "completed", ("committed", "delivered"))
    elif status == "cancelled":
        db.set_links_for_ticket(ticket_id, "cancelled", ("committed",))
    db.set_ticket(ticket_id, **fields)
    db.add_event(ticket_id, "status", f"Status: {STATUS_LABELS[ticket['status']]} → {STATUS_LABELS[status]}."
                 + (f" {note}" if note else ""), who)
    if refresh_case(ticket["thread_id"], who) == "resolved":
        db.add_event(ticket_id, "status", "Every need of this school is confirmed: case resolved.", who)


def refresh_all_cases() -> int:
    """At startup: resolve cases whose needs were all confirmed before case resolution existed (or by a script)."""
    n = 0
    for t in db.list_threads("approved") + db.list_threads("resolved"):
        if refresh_case(t["id"], backfill=True):
            n += 1
    return n


def refresh_case(thread_id: int, who: str = "staff", backfill: bool = False) -> str | None:
    """Keep the case in step with its needs: once every need is confirmed by the school (or cancelled, with at
    least one confirmed), the case is resolved and counted on Impact. If a need reopens, the case is active again."""
    thread = db.get_thread(thread_id)
    if not thread or thread["status"] not in ("approved", "resolved"):
        return None
    tickets = db.list_tickets(thread_id=thread_id)
    live = [k for k in tickets if k["status"] != "cancelled"]
    done = bool(live) and all(k["status"] == "checked" for k in live)
    if done and thread["status"] == "approved":
        when = db.now() if not backfill else max((k.get("delivered_at") or "" for k in live), default="") or db.now()
        db.set_thread(thread_id, status="resolved", resolved_at=when)
        return "resolved"
    if not done and thread["status"] == "resolved":
        db.set_thread(thread_id, status="approved", resolved_at=None)
        return "reopened"
    return None


def step_done(ticket_id: int, step_id: int, who: str) -> None:
    step = db.get_step(step_id)
    if not step or step["ticket_id"] != ticket_id or step["done_at"]:
        return
    db.set_step(step_id, done_at=db.now(), done_by=who)
    db.add_event(ticket_id, "step", f"Done: {step['title']}.", who)
    steps = db.list_steps(ticket_id)
    ticket = db.get_ticket(ticket_id)
    if step["title"].startswith(CONFIRM_PREFIX):
        set_status(ticket_id, "checked", who, "The school confirmed the work.")
        return
    work = [s for s in steps if not s["title"].startswith(CONFIRM_PREFIX) and s["title"] != "Check use after 1 month"]
    if all(s["done_at"] for s in work) and ticket["status"] not in ("delivered", "checked"):
        set_status(ticket_id, "delivered", who)
    elif ticket["status"] in ("new", "matched"):
        set_status(ticket_id, "in_progress", who)


def add_quote(ticket_id: int, amount: float, who: str, *, donor_id: int | None = None,
              supplier_name: str | None = None, quantity: float | None = None, unit: str | None = None,
              note: str | None = None) -> dict:
    qid = db.add_quote(ticket_id, amount, donor_id=donor_id, supplier_name=supplier_name, quantity=quantity,
                       unit=unit, note=note, added_by=who)
    quotes = db.list_quotes()
    q = next(x for x in quotes if x["id"] == qid)
    check = matching.price_check(q, quotes)
    db.add_event(ticket_id, "note", f"Quote from {q['who']}: {amount:,.0f} FCFA"
                 + (f" for {quantity:g} {unit or ''}".rstrip() if quantity else "") + f" ({check['label']}).", who)
    return check


def quotes_view(ticket_id: int) -> list[dict]:
    quotes = db.list_quotes()
    return [{**q, "check": matching.price_check(q, quotes)} for q in quotes if q["ticket_id"] == ticket_id]


# ---------------------------------------------------------------- messages

def _lang_for_thread(thread_id: int | None) -> str:
    s = db.latest_approved_summary(thread_id) if thread_id else None
    return gaps.message_language(s["data"]) if s else "en"


def partner_message(ticket: dict, donor: dict, item_desc: str | None = None) -> str:
    s = db.latest_approved_summary(ticket["thread_id"])
    org = (s["data"].get("organization") if s else {}) or {}
    place = ", ".join(x for x in (org.get("town"), org.get("region")) if x)
    school = org.get("name") or ticket.get("case_title") or "a school"
    qty = f" ({ticket['quantity_text']})" if ticket.get("quantity_text") else ""
    if _lang_for_thread(donor.get("thread_id")) == "fr":
        return (f"PS43 : Bonjour {donor['name']}, merci encore pour votre offre"
                + (f" ({item_desc})" if item_desc else "") + f". Une école en a besoin : {school}"
                + (f" ({place})" if place else "") + f" - {ticket['description']}{qty}. "
                "Pouvez-vous confirmer ce que vous pouvez fournir, et quand ? Répondez ici.")
    return (f"PS43: Hello {donor['name']}, thank you again for your offer" + (f" ({item_desc})" if item_desc else "")
            + f". A school needs it: {school}" + (f" ({place})" if place else "")
            + f" - {ticket['description']}{qty}. Could you confirm what you can provide, and when? Reply here.")


def school_update_message(ticket: dict) -> str:
    names = sorted({l["donor_name"] for l in db.list_links(ticket_id=ticket["id"]) if l["status"] != "cancelled"})
    who = " & ".join(names) or ("un partenaire" if _lang_for_thread(ticket["thread_id"]) == "fr" else "a partner")
    if _lang_for_thread(ticket["thread_id"]) == "fr":
        return (f"PS43 : Bonjour ! Bonne nouvelle : {who} va aider pour « {ticket['description']} ». "
                "Nous vous enverrons les prochaines étapes. Merci de nous envoyer des photos à chaque étape.")
    return (f"PS43: Hello! Good news: {who} will help with \"{ticket['description']}\". "
            "We will send you the next steps. Please send us photos at each step.")


PERIODS = {"fr": {30: "1 mois", 90: "3 mois", 180: "6 mois"}, "en": {30: "1 month", 90: "3 months", 180: "6 months"}}


def followup_message(f: dict) -> str:
    lang = _lang_for_thread(f["thread_id"])
    period = PERIODS[lang].get(f["days"], f"{f['days']} days")
    if lang == "fr":
        return (f"PS43 : Bonjour ! Il y a {period}, « {f['description']} » a été livré à votre école.\n"
                "1. Est-ce toujours utilisé et en bon état ?\n2. Combien d'élèves en profitent ?\n"
                "3. Y a-t-il un problème à régler ?\nVous pouvez répondre par message, photo ou note vocale.")
    return (f"PS43: Hello! {period.capitalize()} ago, \"{f['description']}\" was delivered to your school.\n"
            "1. Is it still in use and in good condition?\n2. How many pupils benefit from it?\n"
            "3. Is there any problem to fix?\nYou can reply by text, photo or voice note.")


class SendFailed(Exception):
    pass


def send(contact_id: int, thread_id: int | None, text: str, who: str, ticket_id: int | None,
         manual: bool = True, label: str = "Message") -> str:
    """Record a message staff sent from their own phone (WhatsApp or SMS) on the ticket and in the case."""
    contact = db.get_contact(contact_id)
    if contact["consent_status"] == "opted_out":
        raise SendFailed("This contact opted out.")
    to = contact.get("reply_to") or contact["address"]
    how = "sent from a staff phone"
    if thread_id:
        db.add_item(contact_id=contact_id, channel="whatsapp" if to.startswith("whatsapp:") else "sms", kind="text",
                    direction="out", author=who, body=f"[{label}] {text.strip()}", status="processed", thread_id=thread_id)
    if ticket_id:
        db.add_event(ticket_id, "message", f"{label} to {contact.get('display_name') or to} ({how}): {text.strip()}", who)
    return how


def followups_due(today: str | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date().isoformat()
    out = []
    for f in db.list_followups(due_by=today, unsent_only=True):
        c = db.get_contact(f["contact_id"]) or {}
        out.append({**f, "message": followup_message(f), "phone": db.phone_of(c.get("reply_to") or c.get("address"))})
    return out



# ---------------------------------------------------------------- Today: one to-do list

STALL_DAYS = 14


def _days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).days


def today_view() -> dict:
    """What the team should do now, most important first. Each item has one obvious action."""
    drafts = sorted(db.list_threads(status="draft_ready"), key=lambda t: t["last_item_at"])
    followups = followups_due()
    cov = coverage_view()
    ready = [r for r in cov["rows"] if r["ticket"]["status"] == "new" and r["plan"]["type"] in ("single", "combined")]
    no_partner = [r for r in cov["rows"] if r["plan"]["type"] == "none"]
    quotes = db.list_quotes()
    open_ids = {k["id"]: k for k in db.list_tickets() if k["status"] in ("new", "matched", "in_progress")}
    flagged = []
    for q in quotes:
        if q["ticket_id"] in open_ids and not q["chosen"]:
            check = matching.price_check(q, quotes)
            if check["status"] == "high":
                flagged.append({**q, "check": check, "ticket": open_ids[q["ticket_id"]]})
    stalled = []
    for k in db.list_tickets():
        if k["status"] in ("matched", "in_progress"):
            events = db.list_events(k["id"])
            quiet = _days_since(events[-1]["at"] if events else k["updated_at"])
            if quiet is not None and quiet >= STALL_DAYS:
                stalled.append({**k, "quiet_days": quiet})
    m = matrix_view()
    act_now = [c for c in m["boxes"]["act_now"] if c["open_tickets"]]
    names = school_names()
    for r in ready + no_partner:
        r["ticket"]["school"] = names.get(r["ticket"]["thread_id"]) or r["ticket"].get("school")
    for f in followups:
        f["school"] = names.get(f["thread_id"]) or f.get("display_name")
    for k in stalled:
        k["school"] = names.get(k["thread_id"]) or k.get("display_name")
    # delivered, waiting for the school to say it arrived: one click resolves the need (and the case if it was the last)
    confirm = []
    for k in db.list_tickets(status="delivered"):
        step = next((x for x in db.list_steps(k["id"]) if x["title"].startswith(CONFIRM_PREFIX) and not x["done_at"]), None)
        if step:
            confirm.append({**k, "school": names.get(k["thread_id"]) or k.get("display_name"), "step_id": step["id"]})
    count = len(drafts) + len(followups) + len(ready) + len(flagged) + len(stalled) + len(confirm)
    return {"drafts": drafts, "followups": followups, "ready": ready, "flagged": flagged, "stalled": stalled,
            "confirm": confirm, "act_now": act_now, "no_partner": no_partner, "count": count,
            "hold": m["boxes"]["safety_hold"], "resolved": resolved_cases()[:4]}


# ---------------------------------------------------------------- Impact: the donor report

def impact_view() -> dict:
    tickets = db.list_tickets()
    threads = {t["id"]: t for t in db.list_threads()}
    cases = {c["thread"]["id"]: c for c in approved_cases()}
    delivered = [k for k in tickets if k["status"] in ("delivered", "checked")]
    confirmed = [k for k in delivered if k["status"] == "checked"]
    helped_threads = {k["thread_id"] for k in delivered}
    pupils = sum((cases[t]["score"]["students"] or 0) for t in helped_threads if t in cases)
    days = []
    for k in delivered:
        start = threads.get(k["thread_id"], {}).get("opened_at")
        if start and k.get("delivered_at"):
            d = (datetime.fromisoformat(k["delivered_at"]) - datetime.fromisoformat(start)).days
            days.append(max(0, d))
    days.sort()
    median_days = days[len(days) // 2] if days else None

    quotes = db.list_quotes()
    questioned = 0.0
    flagged_quotes = 0
    for q in quotes:
        c = matching.price_check(q, quotes)
        if c["status"] == "high" and c.get("median") and q.get("quantity"):
            questioned += q["amount"] - c["median"] * q["quantity"]
            flagged_quotes += 1

    partners = partners_view()
    trust_counts = {lvl: sum(1 for p in partners if p["trust"]["level"] == lvl) for lvl in ("Trusted", "Verified", "New")}
    links = db.list_links()
    by_region: dict[str, dict] = {}
    for c in cases.values():
        r = c["score"]["region"] or "Unknown"
        row = by_region.setdefault(r, {"region": r, "schools": 0, "open": 0, "delivered": 0, "pupils": 0})
        row["schools"] += 1
        mine = [k for k in tickets if k["thread_id"] == c["thread"]["id"]]
        row["open"] += sum(1 for k in mine if k["status"] in ("new", "matched", "in_progress"))
        done = sum(1 for k in mine if k["status"] in ("delivered", "checked"))
        row["delivered"] += done
        if done:
            row["pupils"] += c["score"]["students"] or 0
    by_category: dict[str, int] = {}
    for k in delivered:
        label = matching.CATEGORY_LABELS.get(k["category"], k["category"])
        by_category[label] = by_category.get(label, 0) + 1
    names = school_names()
    stories = []
    for k in sorted(confirmed, key=lambda k: k.get("delivered_at") or "", reverse=True)[:8]:
        who = sorted({l["donor_name"] for l in links if l["ticket_id"] == k["id"] and l["status"] != "cancelled"})
        c = cases.get(k["thread_id"])
        stories.append({"ticket": k, "school": names.get(k["thread_id"]) or k["case_title"], "partners": who,
                        "pupils": c["score"]["students"] if c else None,
                        "region": c["score"]["region"] if c else None})
    sent_followups = [f for f in db.list_followups() if f["sent_at"]]
    resolved = resolved_cases()
    # month by month: needs confirmed by schools, running total (the "results accumulating" chart)
    months: dict[str, int] = {}
    for k in confirmed:
        m = (k.get("delivered_at") or "")[:7]
        if m:
            months[m] = months.get(m, 0) + 1
    timeline, running = [], 0
    for m in sorted(months):
        running += months[m]
        timeline.append({"month": m, "added": months[m], "total": running})
    awaiting = [{"ticket": k, "school": school_names().get(k["thread_id"]) or k["case_title"]}
                for k in delivered if k["status"] == "delivered"]
    return {
        "resolved": resolved, "resolved_pupils": sum(r["pupils"] or 0 for r in resolved),
        "timeline": timeline[-12:], "awaiting": awaiting,
        "schools": len(cases), "schools_helped": len(helped_threads), "pupils": pupils,
        "needs_total": len([k for k in tickets if k["status"] != "cancelled"]), "delivered": len(delivered),
        "confirmed": len(confirmed), "confirmed_rate": round(100 * len(confirmed) / len(delivered)) if delivered else None,
        "median_days": median_days, "questioned": round(questioned), "flagged_quotes": flagged_quotes,
        "partners": len(partners), "trust_counts": trust_counts,
        "partners_completed": sum(1 for p in partners if p["completed"]),
        "by_region": sorted(by_region.values(), key=lambda r: -r["schools"]),
        "by_category": sorted(by_category.items(), key=lambda kv: -kv[1]), "stories": stories,
        "followups_sent": len(sent_followups), "cost": cost_view(len(cases)),
    }


COST_KEYS = ("per_ai_draft", "per_message", "monthly_hosting")


def cost_view(school_cases: int) -> dict | None:
    """Cost per case from the unit prices staff enter in Settings (nothing is assumed)."""
    costs = db.get_setting("costs", {}) or {}
    if not any(costs.get(k) for k in COST_KEYS):
        return None
    n = db.activity_counts()
    months = max(1, (_days_since(n["first_contact"]) or 0) // 30 + 1)
    parts = {
        "AI drafts": n["ai_drafts"] * float(costs.get("per_ai_draft") or 0),
        "WhatsApp/SMS messages": n["messages"] * float(costs.get("per_message") or 0),
        "Hosting": months * float(costs.get("monthly_hosting") or 0),
    }
    total = sum(parts.values())
    return {"currency": costs.get("currency") or "USD", "parts": parts, "total": total, "counts": n,
            "months": months, "per_case": total / n["cases"] if n["cases"] else None}


# ---------------------------------------------------------------- the journey and the one next step

TICKET_STAGE = {"new": "match", "matched": "deliver", "in_progress": "deliver", "delivered": "confirm",
                "checked": "confirm", "cancelled": None}


def journey_counts() -> dict:
    threads = db.list_threads()
    tickets = db.list_tickets()
    return {
        "request": sum(1 for t in threads if t["status"] == "open"),
        "check": sum(1 for t in threads if t["status"] == "draft_ready"),
        "rank": len(matrix_view()["cases"]),
        "match": sum(1 for k in tickets if k["status"] == "new"),
        "deliver": sum(1 for k in tickets if k["status"] in ("matched", "in_progress")),
        "confirm": sum(1 for k in tickets if k["status"] == "delivered"),
    }


def case_stage(thread: dict, tickets: list[dict]) -> str | None:
    if thread["status"] == "open":
        return "request"
    if thread["status"] == "draft_ready":
        return "check"
    if thread["status"] == "resolved":
        return "confirm"
    if thread["status"] != "approved":
        return None
    order = ["match", "deliver", "confirm"]
    stages = [TICKET_STAGE.get(k["status"]) for k in tickets if TICKET_STAGE.get(k["status"])]
    open_stages = [TICKET_STAGE[k["status"]] for k in tickets if k["status"] not in ("checked", "cancelled")]
    if open_stages:
        return min(open_stages, key=order.index)  # the step that still needs work
    return "confirm" if stages else "rank"


def ticket_next_step(t: dict, plan: dict, steps: list[dict]) -> dict | None:
    """The single most useful action on a need, as a button."""
    status = t["status"]
    if status == "new":
        if plan["type"] in ("single", "combined", "partial") and plan["picks"]:
            who = " + ".join(p["donor_name"] for p in plan["picks"])
            return {"label": f"Confirm {who}", "hint": "They match this need on type of help, place and quantity.",
                    "action": f"/tickets/{t['id']}/assign-plan", "button": "Confirm partner"}
        return {"label": "No partner yet", "hint": "Add one PS43 knows, or share the sign-up link where this help is needed.",
                "href": "/partners", "button": "Find a partner"}
    if status in ("matched", "in_progress"):
        todo = [s for s in steps if not s["done_at"] and not s["title"].startswith(CONFIRM_PREFIX)
                and s["title"] != "Check use after 1 month"]
        if status == "matched" and not any(e["kind"] == "message" for e in db.list_events(t["id"])):
            return {"label": "Tell the partner", "hint": "Send the prepared message below, then log the first step.",
                    "href": "#message", "button": "Write the message"}
        if todo:
            return {"label": f"Mark done: {todo[0]['title']}", "hint": f"Step {todo[0]['position']} of {len(steps)}.",
                    "action": f"/tickets/{t['id']}/steps/{todo[0]['id']}/done", "button": "Done"}
    if status == "delivered":
        confirm = next((s for s in steps if s["title"].startswith(CONFIRM_PREFIX) and not s["done_at"]), None)
        if confirm:
            return {"label": "Did the school confirm, with photos?", "hint": "Only a school confirmation raises the partner's trust.",
                    "action": f"/tickets/{t['id']}/steps/{confirm['id']}/done", "button": "Yes, school confirmed"}
    if status == "checked":
        return {"label": "Done: confirmed by the school", "hint": "Follow-ups go out at 30, 90 and 180 days (see Today)."}
    return None


def case_next_step(thread: dict, summary: dict | None, tickets: list[dict], partner: dict | None) -> dict | None:
    if thread["status"] == "draft_ready" and summary:
        return {"label": "Check the draft against the messages, then approve",
                "hint": "Click the I-numbers to see where each fact comes from.", "href": "#decide", "button": "Review and approve"}
    if thread["status"] == "open":
        return {"label": "Collecting messages", "hint": "The AI writes a draft once the messages are processed."}
    if thread["status"] == "resolved":
        return {"label": "Resolved: every need is confirmed by the school",
                "hint": "It counts on Impact. Follow-ups at 30, 90 and 180 days show up on Today.", "href": "/impact",
                "button": "See Impact"}
    if thread["status"] != "approved":
        return None
    if partner:
        return {"label": f"{partner['name']} is in the partners database", "href": f"/partners/{partner['id']}",
                "button": "Open partner"}
    order = {"new": 0, "matched": 1, "in_progress": 2, "delivered": 3}
    open_t = sorted((k for k in tickets if k["status"] in order), key=lambda k: order[k["status"]])
    if open_t:
        k = open_t[0]
        verb = {"new": "Find a partner for", "matched": "Start delivery of", "in_progress": "Keep going:",
                "delivered": "Get the school to confirm"}[k["status"]]
        return {"label": f"{verb} {k['description']}", "hint": f"{len(open_t)} open need(s) for this school.",
                "href": f"/tickets/{k['id']}", "button": "Open need"}
    return {"label": "Nothing open for this school", "hint": "Every need is delivered and confirmed."}


def search(q: str) -> dict:
    q = (q or "").strip().lower()
    if len(q) < 2:
        return {"q": q, "cases": [], "partners": [], "needs": []}
    names = school_names()
    cases = [t for t in db.list_threads() if any(q in (v or "").lower() for v in
             (t["case_number"], t["title"], t["display_name"], t["address"], names.get(t["id"])))]
    partners = [d for d in db.list_donors() if q in d["name"].lower() or q in (d.get("summary") or "").lower()
                or any(q in r.lower() for r in d["regions"])]
    needs = [{**k, "school": names.get(k["thread_id"]) or k["display_name"]} for k in db.list_tickets()
             if q in k["description"].lower() or q in (names.get(k["thread_id"]) or "").lower()
             or q in matching.CATEGORY_LABELS.get(k["category"], "").lower()]
    return {"q": q, "cases": cases[:20], "partners": partners[:20], "needs": needs[:20]}
