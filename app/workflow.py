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
from .channels import twilio

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


def approved_cases() -> list[dict]:
    """Every school case with an approved record (latest approved version).

    Needs already delivered are left out of the need score, so a school moves down the matrix once helped.
    """
    done = {(k["thread_id"], k["need_key"]) for k in db.list_tickets() if k["status"] in ("delivered", "checked")}
    out = []
    for t in db.list_threads():
        if t["status"] in ("rejected", "not_relevant", "closed"):
            continue
        s = db.latest_approved_summary(t["id"])
        if not s or gaps.bank_for(s["data"]) != "school":
            continue
        data = s["data"]
        helped = [n for n in data.get("needs") or [] if (t["id"], _key(n)) in done]
        scored = {**data, "needs": [n for n in data.get("needs") or [] if n not in helped]}
        out.append({"thread": t, "summary": s, "data": data, "score": scoring.score_case(scored),
                    "helped": [n["description"] for n in helped]})
    return out


def school_names() -> dict[int, str]:
    out = {}
    for t in db.list_threads():
        s = db.latest_approved_summary(t["id"])
        if s:
            out[t["id"]] = (s["data"].get("organization") or {}).get("name") or t["title"]
    return out


def matrix_view() -> dict:
    cases = approved_cases()
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
         manual: bool = False, label: str = "Message") -> str:
    """Send on WhatsApp/SMS (or record that staff sent it from their phone) and log it on the ticket and case."""
    contact = db.get_contact(contact_id)
    if contact["consent_status"] == "opted_out":
        raise SendFailed("This contact opted out.")
    to = contact.get("reply_to") or contact["address"]
    sid = None
    if not manual:
        try:
            sid = twilio.send_message(to, text.strip())
        except twilio.SendError as exc:
            raise SendFailed(str(exc)) from exc
    how = "sent from a staff phone" if manual else ("WhatsApp" if to.startswith("whatsapp:") else "SMS")
    if thread_id:
        db.add_item(contact_id=contact_id, channel="whatsapp" if to.startswith("whatsapp:") else "sms", kind="text",
                    direction="out", author=who, body=f"[{label}] {text.strip()}", external_id=sid or None,
                    status="processed", thread_id=thread_id)
    if ticket_id:
        db.add_event(ticket_id, "message", f"{label} to {contact.get('display_name') or to} ({how}): {text.strip()}", who)
    return how


def followups_due(today: str | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date().isoformat()
    return [{**f, "message": followup_message(f)} for f in db.list_followups(due_by=today, unsent_only=True)]
