"""Part two screens: priority matrix, tickets, partners & donors, matching, follow-ups (staff only)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import db, geo, matching, scoring, workflow
from .summary import DONOR_KINDS, NEED_CATEGORIES
from .web import _ctx, staff, templates

router = APIRouter(dependencies=[Depends(staff)])


def _qty(value) -> str:
    """60 -> '60', 2.5 -> '2.5', 3000000 -> '3,000,000'."""
    if value is None or value == "":
        return ""
    v = float(value)
    return f"{v:,.0f}" if v.is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")


templates.env.filters["qty"] = _qty

ROLE_LABELS = {"donor": "Donor", "implementer": "Does the work", "supplier": "Supplier", "shipper": "Shipping"}
PLAN_LABELS = {"none": "No partner yet", "related": "Related help only: check", "partial": "Partly covered",
               "single": "One partner can cover it",
               "combined": "Covered by combining partners", "covered": "Fully committed"}


def _render(request: Request, name: str, **kw):
    return templates.TemplateResponse(request, name, _ctx(
        request, status_labels=workflow.STATUS_LABELS, category_labels=matching.CATEGORY_LABELS,
        role_labels=ROLE_LABELS, plan_labels=PLAN_LABELS, **kw))


def _go(url: str, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode
    q = {k: v for k, v in {"msg": msg, "error": error}.items() if v}
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url + ((sep + urlencode(q)) if q else ""), status_code=303)


def _num(text: str) -> float | None:
    text = (text or "").strip().replace(" ", "").replace(",", "")
    try:
        return float(text) if text else None
    except ValueError:
        return None


# ------------------------------------------------------------------ dashboard

W, H, L, B = 560, 380, 44, 36  # matrix drawing size and margins


def _plot(cases: list[dict]) -> list[dict]:
    """Dot positions and non-overlapping labels for the matrix."""
    pts = []
    for c in cases:
        if c["score"]["quadrant"] == "safety_hold":
            continue
        s = c["score"]
        x = L + 10 + s["readiness"] / 100 * (W - L - 28)
        y = H - B - 10 - s["need"] / 100 * (H - B - 28)
        name = s["name"] if len(s["name"]) <= 22 else s["name"][:21].rstrip() + "…"
        left = s["readiness"] > 60
        width = 6.3 * len(name)
        x0, x1 = (x - 11 - width, x - 11) if left else (x + 11, x + 11 + width)
        pts.append({"c": c, "x": x, "y": y, "r": 6 + s["reach"] / 6, "name": name, "left": left,
                    "lx": x - 11 if left else x + 11, "ly": y + 4, "span": (x0, x1)})
    placed = []
    for p in sorted(pts, key=lambda p: p["y"]):
        base = p["ly"]
        for off in (0, -14, 14, -24, 24, -36, 36, 48):  # nearest free spot above or below the dot
            ly = base + off
            clash = [q for q in placed if abs(q["ly"] - ly) < 14 and q["span"][0] < p["span"][1]
                     and p["span"][0] < q["span"][1]]
            clash += [q for q in pts if q is not p and abs(q["y"] - (ly - 4)) < q["r"] + 6
                      and p["span"][0] < q["x"] + q["r"] and q["x"] - q["r"] < p["span"][1]]
            if not clash:
                p["ly"] = ly
                break
        placed.append(p)
    return pts


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, region: str = "", msg: str | None = None, error: str | None = None):
    view = workflow.matrix_view()
    regions = sorted({c["score"]["region"] for c in view["cases"] if c["score"]["region"]})
    if region:
        view["cases"] = [c for c in view["cases"] if c["score"]["region"] == region]
        view["boxes"] = {q: [c for c in v if c["score"]["region"] == region] for q, v in view["boxes"].items()}
    tickets = db.list_tickets()
    open_t = [k for k in tickets if k["status"] in workflow.OPEN_STATUSES]
    coverage = workflow.coverage_view()
    matched = sum(coverage["counts"][t] for t in ("single", "combined", "covered"))
    kpis = {
        "schools": len(view["cases"]), "act_now": len(view["boxes"]["act_now"]),
        "hold": len(view["boxes"]["safety_hold"]), "open_needs": len(open_t),
        "match_rate": round(100 * matched / len(coverage["rows"])) if coverage["rows"] else None,
        "in_progress": sum(1 for k in tickets if k["status"] in ("matched", "in_progress")),
        "delivered": sum(1 for k in tickets if k["status"] in ("delivered", "checked")),
        "partners": len(db.list_donors()), "helped": len(db.list_threads("resolved")),
    }
    return _render(request, "dashboard.html", view=view, kpis=kpis, regions=regions, region=region,
                   plot=_plot(view["cases"]), W=W, H=H, L=L, B=B, geo=geo.map_view(view["cases"]),
                   followups=workflow.followups_due(), msg=msg, error=error)


@router.get("/api/matrix")
def api_matrix():
    view = workflow.matrix_view()
    return JSONResponse([{"case_number": c["thread"]["case_number"], "thread_id": c["thread"]["id"], **{
        k: v for k, v in c["score"].items() if k != "location"}, "location": c["score"]["location"],
        "open_tickets": c["open_tickets"]} for c in view["cases"]])


# ------------------------------------------------------------------ tickets

@router.get("/tickets", response_class=HTMLResponse)
def tickets_board(request: Request, msg: str | None = None, error: str | None = None):
    coverage = {r["ticket"]["id"]: r["plan"]["type"] for r in workflow.coverage_view()["rows"]}
    links = db.list_links()
    cols = {s: [] for s in workflow.STATUSES if s != "cancelled"}
    names = workflow.school_names()
    for k in db.list_tickets():
        k["school"] = names.get(k["thread_id"]) or k["display_name"] or k["case_title"]
        if k["status"] in cols:
            k["partners"] = sorted({l["donor_name"] for l in links if l["ticket_id"] == k["id"] and l["status"] != "cancelled"})
            k["plan_type"] = coverage.get(k["id"])
            cols[k["status"]].append(k)
    urgency = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    ready_first = {"single": 0, "combined": 0, "partial": 1, "related": 2, "none": 3}
    for status, v in cols.items():
        if status == "new":  # the ones a partner can cover right away first
            v.sort(key=lambda k: (ready_first.get(k.get("plan_type"), 4), urgency.get(k["urgency"], 5), k["id"]))
        elif status == "checked":  # newest confirmations first
            v.sort(key=lambda k: k.get("delivered_at") or "", reverse=True)
        else:
            v.sort(key=lambda k: (urgency.get(k["urgency"], 5), k["id"]))
    for k in cols["matched"] + cols["in_progress"] + cols["delivered"]:
        steps = db.list_steps(k["id"])
        k["done"], k["total"] = sum(1 for x in steps if x["done_at"]), len(steps)
    lanes = {"waiting": cols["new"], "moving": cols["delivered"] + cols["in_progress"] + cols["matched"], "done": cols["checked"]}
    return _render(request, "tickets.html", cols=cols, lanes=lanes, msg=msg, error=error)


def _ticket_or_404(ticket_id: int) -> dict:
    t = db.get_ticket(ticket_id)
    if not t:
        raise HTTPException(404)
    return t


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_page(request: Request, ticket_id: int, msg: str | None = None, error: str | None = None):
    t = _ticket_or_404(ticket_id)
    m = workflow.ticket_matches(t)
    s = db.latest_approved_summary(t["thread_id"])
    score = scoring.score_case(s["data"]) if s else None
    donors = db.list_donors()
    linked = [l for l in m["links"] if l["status"] != "cancelled"]
    first = next((d for d in donors if d["id"] == (linked[0]["donor_id"] if linked else
                                                     (m["plan"]["picks"][0]["donor_id"] if m["plan"]["picks"] else -1))), None)
    return _render(
        request, "ticket.html", t=t, m=m, score=score, steps=db.list_steps(ticket_id), events=db.list_events(ticket_id),
        quotes=workflow.quotes_view(ticket_id), followups=db.list_followups(ticket_id=ticket_id), donors=donors,
        linked=linked, partner_text=workflow.partner_message(t, first) if first else "",
        partner_default=first["id"] if first else None, school_text=workflow.school_update_message(t),
        partner_texts={d["id"]: workflow.partner_message(t, d) for d in donors if d.get("contact_id")},
        statuses=workflow.STATUSES, msg=msg, error=error, stage=workflow.TICKET_STAGE.get(t["status"]),
        phones={c: db.phone_of((db.get_contact(c) or {}).get("reply_to") or (db.get_contact(c) or {}).get("address"))
                for c in {d["contact_id"] for d in donors if d.get("contact_id")} | {t["contact_id"]}},
        next=workflow.ticket_next_step(t, m["plan"], db.list_steps(ticket_id)))


@router.post("/tickets/{ticket_id}/status")
def ticket_status(ticket_id: int, status: str = Form(...), back: str = Form(""), who: str = Depends(staff)):
    t = _ticket_or_404(ticket_id)
    before = (db.get_thread(t["thread_id"]) or {}).get("status")
    workflow.set_status(ticket_id, status, who)
    return _go(_safe_back(back, f"/tickets/{ticket_id}"),
               msg=_outcome_msg(ticket_id, before) or f"Status set to {workflow.STATUS_LABELS.get(status, status)}.")


@router.post("/tickets/{ticket_id}/assign")
def ticket_assign(ticket_id: int, donor_id: int = Form(...), item_id: str = Form(""), quantity: str = Form(""),
                  back: str = Form(""), who: str = Depends(staff)):
    t = _ticket_or_404(ticket_id)
    workflow.assign(ticket_id, donor_id, int(item_id) if item_id.strip() else None, _num(quantity), who)
    donor = db.get_donor(donor_id) or {}
    return _go(_safe_back(back, f"/tickets/{ticket_id}"),
               msg=f"{donor.get('name', 'Partner')} assigned to {t['description']}. It moved to 'Partner found' on Needs.")


@router.post("/tickets/{ticket_id}/assign-plan")
def ticket_assign_plan(ticket_id: int, back: str = Form(""), who: str = Depends(staff)):
    t = _ticket_or_404(ticket_id)
    plan = workflow.ticket_matches(t)["plan"]
    for p in plan["picks"]:
        workflow.assign(ticket_id, p["donor_id"], p["item_id"], p["covers"], who)
    n = len(plan['picks']); msg = f"{'Partner' if n == 1 else str(n) + ' partners'} confirmed for “{t['description']}”. Next: send them a message."
    return _go(_safe_back(back, f"/tickets/{ticket_id}"), msg=msg)


@router.post("/tickets/{ticket_id}/links/{link_id}/cancel")
def ticket_cancel_link(ticket_id: int, link_id: int, who: str = Depends(staff)):
    workflow.cancel_link(ticket_id, link_id, who)
    return _go(f"/tickets/{ticket_id}", msg="Commitment cancelled.")


def _safe_back(back: str, default: str) -> str:
    return back if back.startswith("/") and not back.startswith("//") else default


def _outcome_msg(ticket_id: int, before_case: str | None) -> str | None:
    """Tell staff what their click changed elsewhere (case resolved, need delivered...)."""
    t = db.get_ticket(ticket_id)
    case = db.get_thread(t["thread_id"]) if t else None
    if case and case["status"] == "resolved" and before_case != "resolved":
        return f"School confirmed. {case['case_number']} is resolved and now counts on Impact."
    if t and t["status"] == "checked":
        return "School confirmed this need. Follow-ups are scheduled on Today."
    if t and t["status"] == "delivered":
        return "Marked delivered. Next: get the school to confirm, with photos."
    return None


@router.post("/tickets/{ticket_id}/steps/{step_id}/done")
def ticket_step_done(ticket_id: int, step_id: int, back: str = Form(""), who: str = Depends(staff)):
    t = _ticket_or_404(ticket_id)
    before = (db.get_thread(t["thread_id"]) or {}).get("status")
    workflow.step_done(ticket_id, step_id, who)
    return _go(_safe_back(back, f"/tickets/{ticket_id}"), msg=_outcome_msg(ticket_id, before) or "Step done.")


@router.post("/tickets/{ticket_id}/steps")
def ticket_add_step(ticket_id: int, title: str = Form(...), owner: str = Form("ps43"), due: str = Form(""),
                    who: str = Depends(staff)):
    if title.strip():
        db.add_steps(ticket_id, [(title.strip(), owner)])
        steps = db.list_steps(ticket_id)
        if due.strip():
            db.set_step(steps[-1]["id"], due=due.strip())
        db.add_event(ticket_id, "step", f"Step added: {title.strip()}.", who)
    return _go(f"/tickets/{ticket_id}")


@router.post("/tickets/{ticket_id}/quotes")
def ticket_add_quote(ticket_id: int, amount: str = Form(...), donor_id: str = Form(""), supplier_name: str = Form(""),
                     quantity: str = Form(""), unit: str = Form(""), note: str = Form(""), who: str = Depends(staff)):
    value = _num(amount)
    if value is None:
        return _go(f"/tickets/{ticket_id}", error="Enter the total amount in FCFA.")
    check = workflow.add_quote(ticket_id, value, who, donor_id=int(donor_id) if donor_id.strip() else None,
                               supplier_name=supplier_name.strip() or None, quantity=_num(quantity),
                               unit=unit.strip() or None, note=note.strip() or None)
    return _go(f"/tickets/{ticket_id}", msg=f"Quote saved. Price check: {check['label']}.")


@router.post("/tickets/{ticket_id}/quotes/{quote_id}/choose")
def ticket_choose_quote(ticket_id: int, quote_id: int, who: str = Depends(staff)):
    db.choose_quote(ticket_id, quote_id)
    q = next((x for x in db.list_quotes(ticket_id) if x["id"] == quote_id), None)
    if q:
        db.add_event(ticket_id, "note", f"Quote chosen: {q['who']}, {q['amount']:,.0f} FCFA.", who)
    return _go(f"/tickets/{ticket_id}", msg="Quote chosen.")


@router.post("/tickets/{ticket_id}/message")
async def ticket_message(ticket_id: int, to: str = Form(...), text: str = Form(...), manual: str = Form(""),
                         who: str = Depends(staff)):
    t = _ticket_or_404(ticket_id)
    if not text.strip():
        return _go(f"/tickets/{ticket_id}", error="Nothing to send.")
    if to == "school":
        contact_id, thread_id, label = t["contact_id"], t["thread_id"], "Update to school"
    else:
        donor = db.get_donor(int(to))
        if not donor or not donor.get("contact_id"):
            return _go(f"/tickets/{ticket_id}", error="This partner has no phone number on file.")
        contact_id, thread_id, label = donor["contact_id"], donor.get("thread_id"), "Message to partner"
    try:
        how = await run_in_threadpool(workflow.send, contact_id, thread_id, text, who, ticket_id, bool(manual), label)
    except workflow.SendFailed as exc:
        return _go(f"/tickets/{ticket_id}", error=str(exc))
    return _go(f"/tickets/{ticket_id}", msg=f"Message saved on this need ({how}).")


@router.post("/tickets/{ticket_id}/note")
def ticket_note(ticket_id: int, text: str = Form(...), who: str = Depends(staff)):
    if text.strip():
        db.add_event(ticket_id, "note", text.strip(), who)
    return _go(f"/tickets/{ticket_id}")


# ------------------------------------------------------------------ follow-ups

@router.post("/followups/{followup_id}/send")
async def followup_send(followup_id: int, text: str = Form(...), manual: str = Form(""), back: str = Form("/dashboard"),
                        who: str = Depends(staff)):
    f = db.get_followup(followup_id)
    if not f:
        raise HTTPException(404)
    try:
        await run_in_threadpool(workflow.send, f["contact_id"], f["thread_id"], text, who, f["ticket_id"], bool(manual),
                                f"{f['days']}-day follow-up")
    except workflow.SendFailed as exc:
        return _go(back, error=str(exc))
    db.set_followup(followup_id, sent_at=db.now(), sent_by=who)
    return _go(back, msg="Follow-up logged. The school's answers will arrive in its case.")


# ------------------------------------------------------------------ partners & donors

@router.get("/partners", response_class=HTMLResponse)
def partners_page(request: Request, role: str = "", msg: str | None = None):
    everyone = workflow.partners_view()
    for p in everyone:
        p["can_help"] = len(workflow.partner_can_help(p["id"]))
    role_counts = {r: sum(1 for p in everyone if r in p["roles"]) for r in ROLE_LABELS}
    partners = [p for p in everyone if role in p["roles"]] if role else everyone
    return _render(request, "partners.html", partners=partners, total=len(everyone), role_counts=role_counts, role=role,
                   kinds=DONOR_KINDS, categories=NEED_CATEGORIES, msg=msg)


@router.post("/partners")
def partner_add(name: str = Form(...), phone: str = Form(""), kind: str = Form("unknown"), roles: list[str] = Form([]),
                regions: str = Form(""), summary: str = Form(""), who: str = Depends(staff)):
    """Add a partner PS43 already knows (no conversation needed)."""
    from .channels.staff_intake import contact_address
    address, _ = contact_address(name, phone)
    contact = db.get_or_create_contact(address, "upload", name.strip())
    if db.donor_for_contact(contact["id"]):
        d = db.donor_for_contact(contact["id"])
        return _go(f"/partners/{d['id']}", msg="This partner was already in the database.")
    d = db.upsert_donor(contact["id"], None, name=name.strip(), kind=kind, roles=roles or ["donor"],
                        regions=[r.strip() for r in regions.split(",") if r.strip()], summary=summary.strip() or None,
                        notes=f"Added by {who}")
    return _go(f"/partners/{d['id']}", msg="Partner added. Now add what they can provide.")


def _partner_or_404(donor_id: int) -> dict:
    d = db.get_donor(donor_id)
    if not d:
        raise HTTPException(404)
    return d


@router.get("/partners/{donor_id}", response_class=HTMLResponse)
def partner_page(request: Request, donor_id: int, msg: str | None = None, error: str | None = None):
    _partner_or_404(donor_id)
    p = next(x for x in workflow.partners_view() if x["id"] == donor_id)
    contact = db.get_contact(p["contact_id"]) if p.get("contact_id") else None
    thread = db.get_thread(p["thread_id"]) if p.get("thread_id") else None
    phone = db.phone_of((contact or {}).get("reply_to") or (contact or {}).get("address"))
    return _render(request, "partner.html", p=p, contact=contact, thread=thread, phone=phone,
                   can_help=workflow.partner_can_help(donor_id), categories=NEED_CATEGORIES, msg=msg, error=error)


@router.get("/partners/{donor_id}/logo")
def partner_logo(donor_id: int):
    from fastapi.responses import FileResponse
    from . import media
    d = _partner_or_404(donor_id)
    rel = d.get("logo_path") or ((db.get_contact(d["contact_id"]) or {}).get("avatar_path") if d.get("contact_id") else None)
    if not rel or not media.abs_path(rel).exists():
        raise HTTPException(404)
    path = media.abs_path(rel)
    return FileResponse(path, media_type=media.guess_mime(path.name), headers={"Cache-Control": "max-age=3600"})


@router.post("/partners/{donor_id}/logo")
async def partner_logo_upload(donor_id: int, logo: UploadFile = File(...), who: str = Depends(staff)):
    from . import media
    _partner_or_404(donor_id)
    data = await logo.read()
    if not data:
        return _go(f"/partners/{donor_id}", error="Choose an image file.")
    if len(data) > 5 * 1024 * 1024:
        return _go(f"/partners/{donor_id}", error="That image is too large (5 MB at most).")
    mime = logo.content_type or media.guess_mime(logo.filename or "logo.png")
    if not mime.startswith("image/"):
        return _go(f"/partners/{donor_id}", error="The logo must be an image (PNG, JPG, WebP).")
    db.set_donor(donor_id, logo_path=media.save_bytes(data, mime=mime, filename=logo.filename or "logo"))
    return _go(f"/partners/{donor_id}", msg="Logo saved.")


@router.post("/partners/{donor_id}/verify")
def partner_verify(donor_id: int, verified: str = Form("1"), who: str = Depends(staff)):
    _partner_or_404(donor_id)
    on = verified == "1"
    db.set_donor(donor_id, verified=int(on), verified_by=who if on else None)
    return _go(f"/partners/{donor_id}", msg="Identity check recorded." if on else "Identity check removed.")


@router.post("/partners/{donor_id}/notes")
def partner_notes(donor_id: int, notes: str = Form(""), roles: list[str] = Form([]), who: str = Depends(staff)):
    _partner_or_404(donor_id)
    db.set_donor(donor_id, notes=notes.strip() or None, roles=json.dumps(roles or ["donor"]))
    return _go(f"/partners/{donor_id}", msg="Saved.")


@router.post("/partners/{donor_id}/items")
def partner_add_item(donor_id: int, category: str = Form(...), description: str = Form(...), quantity: str = Form(""),
                     location: str = Form(""), abroad: str = Form(""), availability: str = Form(""),
                     conditions: str = Form("")):
    _partner_or_404(donor_id)
    if description.strip():
        qty, unit = matching.parse_quantity(quantity)
        db.add_donor_item(donor_id, None, {
            "category": category, "description": description.strip(), "quantity_text": quantity.strip() or None,
            "quantity_total": qty, "unit": unit, "availability": availability.strip() or None,
            "conditions": conditions.strip() or None, "location": location.strip() or None, "abroad": bool(abroad)})
    return _go(f"/partners/{donor_id}", msg="Item added.")


@router.post("/partners/{donor_id}/items/{item_id}/withdraw")
def partner_withdraw_item(donor_id: int, item_id: int):
    db.set_donor_item(item_id, status="withdrawn")
    return _go(f"/partners/{donor_id}", msg="Item marked as no longer available.")


@router.get("/api/partners")
def api_partners():
    return JSONResponse([{k: v for k, v in p.items() if k not in ("history",)} for p in workflow.partners_view()])


# ------------------------------------------------------------------ matching overview

@router.get("/matches", response_class=HTMLResponse)
def matches_page(request: Request):
    return _render(request, "matches.html", cov=workflow.coverage_view())


@router.get("/api/matches")
def api_matches():
    cov = workflow.coverage_view()
    return JSONResponse({"by_category": cov["by_category"], "counts": cov["counts"], "needs": [
        {"ticket_id": r["ticket"]["id"], "case_number": r["ticket"]["case_number"], "need": r["ticket"]["description"],
         "category": r["ticket"]["category"], "plan": r["plan"]["type"],
         "partners": [{"name": p["donor_name"], "item": p["item"], "covers": p["covers"], "score": p["score"]}
                      for p in r["plan"]["picks"]]} for r in cov["rows"]]})



# ------------------------------------------------------------------ Today, Impact, Settings

@router.get("/today", response_class=HTMLResponse)
def today(request: Request, msg: str | None = None, error: str | None = None):
    now = datetime.now(timezone.utc)
    return _render(request, "today.html", v=workflow.today_view(), date=now.strftime("%A %d %B %Y"), msg=msg,
                   error=error, counts=workflow.journey_counts())


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = ""):
    return _render(request, "search.html", r=workflow.search(q))


@router.get("/impact", response_class=HTMLResponse)
def impact(request: Request):
    return _render(request, "impact.html", v=workflow.impact_view(),
                   date=datetime.now(timezone.utc).strftime("%d %B %Y"))


@router.get("/api/impact")
def api_impact():
    v = workflow.impact_view()
    v["stories"] = [{"school": s["school"], "need": s["ticket"]["description"], "partners": s["partners"],
                     "pupils": s["pupils"], "delivered_at": s["ticket"]["delivered_at"]} for s in v["stories"]]
    v["resolved"] = [{"case_number": r["thread"]["case_number"], "school": r["school"], "region": r["region"],
                      "pupils": r["pupils"], "delivered": r["delivered"], "partners": r["partners"],
                      "days": r["days"], "resolved_at": r["resolved_at"]} for r in v["resolved"]]
    v["awaiting"] = [{"school": a["school"], "need": a["ticket"]["description"]} for a in v["awaiting"]]
    return JSONResponse(v)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str | None = None):
    return _render(request, "settings.html", cfg=scoring.config(), defaults=scoring.defaults(), regions=geo.REGIONS,
                   area_labels=scoring.AREA_LABELS, readiness_labels=scoring.READINESS_LABELS,
                   costs=db.get_setting("costs", {}) or {}, msg=msg)


@router.post("/settings")
async def settings_save(request: Request, who: str = Depends(staff)):
    form = await request.form()
    if form.get("reset"):
        db.set_setting("scoring", {})
        return _go("/settings", msg="Back to the default weights.")
    def n(key, default=0):
        v = _num(str(form.get(key, "")))
        return default if v is None else v
    d = scoring.defaults()
    db.set_setting("scoring", {
        "need_weights": {k: int(n(f"w_{k}", d["need_weights"][k])) for k in scoring.NEED_WEIGHTS},
        "readiness_points": {k: [int(n(f"r_{k}_full", v[0])), int(n(f"r_{k}_part", v[1]))]
                             for k, v in d["readiness_points"].items()},
        "split": int(n("split", d["split"])),
        "vulnerability_max": int(n("vulnerability_max", d["vulnerability_max"])),
        "crisis_regions": [r for r in geo.REGIONS if form.get(f"crisis_{r}")] if form.get("crisis_form") else d["crisis_regions"],
        "changed_by": who,
    })
    db.set_setting("costs", {"per_ai_draft": _num(str(form.get("per_ai_draft", ""))),
                             "per_message": _num(str(form.get("per_message", ""))),
                             "monthly_hosting": _num(str(form.get("monthly_hosting", ""))),
                             "currency": (str(form.get("currency") or "USD")).strip()[:5]})
    return _go("/settings", msg="Saved. Priorities now use these weights.")
