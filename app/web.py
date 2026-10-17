"""Staff screens: inbox, intake uploads, and the review screen (AI draft + human check)."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import db, gaps, media
from .channels import staff_intake, twilio
from .config import settings
from .processing import pipeline
from .scoring import AREA_LABELS
from .summary import NEED_CATEGORIES, PROFILE_AREAS, PROFILE_KEYS, READINESS_KEYS, normalize, summarize_thread

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_basic = HTTPBasic(auto_error=False)


def staff(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    if not settings.staff_password:
        if settings.hosted:
            raise HTTPException(503, "Set STAFF_PASSWORD in the hosting variables before using the inbox online.")
        return "staff"  # open in local development; set STAFF_PASSWORD before going online
    ok = credentials is not None and secrets.compare_digest(credentials.username, settings.staff_username) \
        and secrets.compare_digest(credentials.password, settings.staff_password)
    if not ok:
        raise HTTPException(401, "login required", headers={"WWW-Authenticate": 'Basic realm="Bridge43"'})
    return credentials.username


router = APIRouter(dependencies=[Depends(staff)])


def _ctx(request: Request, **kw) -> dict:
    return {"request": request, "settings": settings, "open_mode": not settings.staff_password,
            "demo_data": bool(db.get_setting("demo_data", False)), **kw}


def _priority(summary: dict | None) -> dict | None:
    """Matrix box and plain-language reason for an approved school case."""
    from . import gaps, scoring
    if not summary:
        return None
    approved = db.latest_approved_summary(summary["thread_id"])
    if not approved or gaps.bank_for(approved["data"]) != "school":
        return None
    return scoring.score_case(approved["data"])


def _meta(item: dict) -> dict:
    return json.loads(item["extraction_meta"]) if item.get("extraction_meta") else {}


# ------------------------------------------------------------------ inbox

@router.get("/", response_class=HTMLResponse)
def inbox(request: Request, status: str | None = None, q: str = ""):
    threads = db.list_threads(status)
    if not status:
        threads = [t for t in threads if t["status"] != "not_relevant"]
    if q.strip():
        needle = q.strip().lower()
        threads = [t for t in threads if needle in " ".join(
            str(t.get(k) or "") for k in ("case_number", "display_name", "address", "title")).lower()]
    everything = db.list_threads()
    counts = {s: sum(1 for t in everything if t["status"] == s)
              for s in ("open", "draft_ready", "approved", "closed", "rejected", "not_relevant")}
    return templates.TemplateResponse(request, "inbox.html", _ctx(request, threads=threads, status=status, counts=counts, q=q))


# ------------------------------------------------------------------ intake by staff

@router.get("/intake", response_class=HTMLResponse)
def intake_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "intake.html", _ctx(request, error=error))


async def _read(f: UploadFile) -> staff_intake.UploadedFile:
    return staff_intake.UploadedFile(filename=f.filename or "upload", data=await f.read(), content_type=f.content_type)


@router.post("/intake/export")
async def intake_export(request: Request, chat: UploadFile = File(...), contact_name: str = Form(""),
                        contact_phone: str = Form(""), staff_names: str = Form(""), date_order: str = Form("auto")):
    upload = await _read(chat)
    day_first = {"dmy": True, "mdy": False}.get(date_order)
    try:
        result = await run_in_threadpool(
            staff_intake.import_whatsapp_export, upload=upload, contact_name=contact_name.strip() or None,
            contact_phone=contact_phone.strip() or None, staff_names=staff_names.split(","), day_first=day_first)
    except (ValueError, KeyError) as exc:
        return templates.TemplateResponse(request, "intake.html", _ctx(request, error=str(exc)), status_code=400)
    return RedirectResponse(f"/threads/{result['thread_id']}?imported={result['created']}"
                            f"&missing={result['missing_media']}&dupes={result['duplicates']}", status_code=303)


@router.post("/intake/notes")
async def intake_notes(request: Request, contact_name: str = Form(""), contact_phone: str = Form(""),
                       source: str = Form("phone_call"), notes: str = Form(""), staff_name: str = Form(""),
                       files: list[UploadFile] = File(default=[])):
    uploads = [await _read(f) for f in files if f.filename]
    try:
        result = await run_in_threadpool(
            staff_intake.add_staff_notes, contact_name=contact_name.strip() or None,
            contact_phone=contact_phone.strip() or None, source=source, notes=notes, files=uploads,
            staff_name=staff_name.strip() or None)
    except ValueError as exc:
        return templates.TemplateResponse(request, "intake.html", _ctx(request, error=str(exc)), status_code=400)
    return RedirectResponse(f"/threads/{result['thread_id']}", status_code=303)


# ------------------------------------------------------------------ review screen

@router.get("/threads/{thread_id}", response_class=HTMLResponse)
def thread_page(request: Request, thread_id: int, msg: str | None = None, error: str | None = None):
    thread = db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404)
    contact = db.get_contact(thread["contact_id"])
    items = db.thread_items(thread_id)
    for it in items:
        it["meta"] = _meta(it)
    summary = db.latest_summary(thread_id)
    from . import workflow
    case_tickets = db.list_tickets(thread_id=thread_id)
    partner = db.donor_for_contact(contact["id"])
    in_flight = sum(1 for i in items if i["status"] in ("received", "processing", "awaiting_media"))
    new_since = []
    if summary:
        used = set(summary["item_ids"])
        new_since = [i["id"] for i in items if i["id"] not in used and i["status"] in ("processed", "failed")]
    return templates.TemplateResponse(request, "thread.html", _ctx(
        request, thread=thread, contact=contact, items=items, summary=summary, in_flight=in_flight,
        new_since=new_since, msg=msg, error=error, need_categories=NEED_CATEGORIES, readiness_keys=READINESS_KEYS,
        profile_keys=PROFILE_KEYS, profile_areas=PROFILE_AREAS,
        summary_json=json.dumps(summary["data"], indent=2, ensure_ascii=False) if summary else "",
        twilio_enabled=settings.twilio_enabled, can_message=db.is_messageable(contact.get("reply_to") or contact["address"]),
        reply_to=contact.get("reply_to") or contact["address"], channels=sorted({i["channel"] for i in items}),
        other_cases=[t for t in db.list_threads() if t["id"] != thread_id],
        case_tickets=case_tickets, partner=partner, priority=_priority(summary), area_labels=AREA_LABELS,
        stage=workflow.case_stage(thread, case_tickets) if not partner else None,
        next=workflow.case_next_step(thread, summary, case_tickets, partner)))


def _back(thread_id: int, **q: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(f"/threads/{thread_id}" + (f"?{urlencode(q)}" if q else ""), status_code=303)


@router.post("/threads/{thread_id}/summarize")
async def summarize_now(thread_id: int):
    try:
        result = await run_in_threadpool(summarize_thread, thread_id, True)
    except Exception as exc:
        return _back(thread_id, error=f"AI draft failed: {exc}"[:400])
    return _back(thread_id, msg="New AI draft ready." if result else "Nothing to summarise yet.")


@router.post("/threads/{thread_id}/items/{item_id}/retry")
def retry_item(thread_id: int, item_id: int):
    item = db.get_item(item_id)
    if not item or item["thread_id"] != thread_id:
        raise HTTPException(404)
    if item["status"] == "failed" and item["media_path"] or item["kind"] in ("text", "form"):
        db.update_item(item_id, status="received", error=None)
        pipeline.enqueue(item_id)
        return _back(thread_id, msg="Retrying that item.")
    return _back(thread_id, error="This item cannot be retried (its media never arrived).")


def _summary_for(summary_id: int) -> dict:
    s = db.get_summary(summary_id)
    if not s:
        raise HTTPException(404)
    return s


@router.post("/summaries/{summary_id}/edit")
def edit_summary(summary_id: int, title: str = Form(...), case_type: str = Form(...), org_name: str = Form(""),
                 org_type: str = Form("unknown"), region: str = Form(""), town: str = Form(""),
                 situation: str = Form(""), open_questions: str = Form(""), confirmation_text: str = Form(""),
                 who: str = Depends(staff)):
    s = _summary_for(summary_id)
    if s["status"] != "draft":
        return _back(s["thread_id"], error="Only drafts can be edited.")
    d = s["data"]
    d["title"], d["case_type"] = title.strip(), case_type
    d["organization"].update(name=org_name.strip() or None, type=org_type, region=region.strip() or None,
                             town=town.strip() or None)
    d["situation"]["text"] = situation.strip()
    d["open_questions"] = [q.strip("-• ").strip() for q in open_questions.splitlines() if q.strip()]
    d["confirmation_message"]["text"] = confirmation_text.strip()
    gaps.refresh(d)
    d.setdefault("_edited_by", [])
    if who not in d["_edited_by"]:
        d["_edited_by"].append(who)
    db.update_summary(summary_id, data=d)
    db.set_thread(s["thread_id"], title=d["title"])
    return _back(s["thread_id"], msg="Saved your edits.")


@router.post("/summaries/{summary_id}/edit-json")
def edit_summary_json(summary_id: int, data: str = Form(...), who: str = Depends(staff)):
    s = _summary_for(summary_id)
    if s["status"] != "draft":
        return _back(s["thread_id"], error="Only drafts can be edited.")
    try:
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("top level must be an object")
    except ValueError as exc:
        return _back(s["thread_id"], error=f"Not valid JSON: {exc}")
    parsed = gaps.refresh(normalize(parsed))
    parsed.setdefault("_edited_by", [])
    if who not in parsed["_edited_by"]:
        parsed["_edited_by"].append(who)
    db.update_summary(summary_id, data=parsed)
    return _back(s["thread_id"], msg="Saved your edits.")


@router.post("/summaries/{summary_id}/approve")
def approve(summary_id: int, notes: str = Form(""), who: str = Depends(staff)):
    s = _summary_for(summary_id)
    if s["status"] != "draft":
        return _back(s["thread_id"], error="This draft was already reviewed.")
    case_number = db.get_thread(s["thread_id"])["case_number"]
    db.update_summary(summary_id, status="approved", reviewer=who, review_notes=notes.strip() or None,
                      reviewed_at=db.now(), case_number=case_number)
    db.set_thread(s["thread_id"], status="approved", stale_summary=0)
    from . import workflow
    made = workflow.on_case_approved(s["thread_id"], who)
    extra = ""
    if made.get("tickets"):
        extra = f" {len(made['tickets'])} new ticket(s) opened for its needs."
    elif made.get("partner"):
        extra = f" {made['partner']['name']} is in the partners database."
    return _back(s["thread_id"], msg=f"Case {case_number} approved.{extra} New messages from this contact keep coming into it.")


@router.post("/summaries/{summary_id}/reject")
def reject(summary_id: int, notes: str = Form(""), who: str = Depends(staff)):
    s = _summary_for(summary_id)
    if s["status"] != "draft":
        return _back(s["thread_id"], error="This draft was already reviewed.")
    db.update_summary(summary_id, status="rejected", reviewer=who, review_notes=notes.strip() or None,
                      reviewed_at=db.now())
    db.set_thread(s["thread_id"], status="rejected")
    return _back(s["thread_id"], msg="Rejected. New messages from this contact will open a new case.")


async def _send_to_contact(summary_id: int, text: str, who: str, label: str) -> tuple[dict, str | None]:
    """Send a WhatsApp/SMS to the case contact and log it in the thread. Returns (thread, error)."""
    s = _summary_for(summary_id)
    thread = db.get_thread(s["thread_id"])
    contact = db.get_contact(thread["contact_id"])
    if contact["consent_status"] == "opted_out":
        return thread, "This contact opted out."
    try:
        sid = await run_in_threadpool(twilio.send_message, contact.get("reply_to") or contact["address"], text.strip())
    except twilio.SendError as exc:
        return thread, f"Not sent: {exc}"
    to = contact.get("reply_to") or contact["address"]
    db.add_item(contact_id=contact["id"], channel="whatsapp" if to.startswith("whatsapp:") else "sms",
                kind="text", direction="out", author=who, body=f"[{label}] {text.strip()}",
                external_id=sid or None, status="processed", thread_id=thread["id"])
    return thread, None


@router.post("/summaries/{summary_id}/send-confirmation")
async def send_confirmation(summary_id: int, text: str = Form(...), who: str = Depends(staff)):
    thread, error = await _send_to_contact(summary_id, text, who, "Confirmation sent")
    if error:
        return _back(thread["id"], error=error)
    db.update_summary(summary_id, confirmation_sent_at=db.now())
    return _back(thread["id"], msg="Confirmation sent. The contact's reply will appear in this thread.")


@router.post("/summaries/{summary_id}/send-follow-up")
async def send_follow_up(summary_id: int, text: str = Form(...), who: str = Depends(staff)):
    """Ask the contact the missing profile questions. Their answers come back into this case and the next
    draft fills the gaps (and drops those questions from the list)."""
    if not text.strip():
        return _back(_summary_for(summary_id)["thread_id"], error="Nothing to send.")
    thread, error = await _send_to_contact(summary_id, text, who, "Follow-up questions sent")
    if error:
        return _back(thread["id"], error=error)
    return _back(thread["id"], msg="Questions sent. Answers will appear in this thread and update the profile.")


# ------------------------------------------------------------------ organising cases

@router.post("/threads/{thread_id}/relevance")
def set_relevance(thread_id: int, relevant: str = Form(...)):
    t = db.get_thread(thread_id) or _404()
    if relevant == "yes":
        s = db.latest_summary(thread_id)
        status = "approved" if s and s["status"] == "approved" else ("draft_ready" if s else "open")
        db.set_thread(thread_id, status=status, relevance_locked=1)
        return _back(thread_id, msg=f"{t['case_number']} is a case again and stays in the inbox.")
    db.set_thread(thread_id, status="not_relevant", relevance_locked=1)
    return _back(thread_id, msg=f"{t['case_number']} moved to Not relevant.")


@router.post("/threads/{thread_id}/close")
def close_case(thread_id: int):
    t = db.get_thread(thread_id) or _404()
    db.set_thread(thread_id, status="closed")
    return _back(thread_id, msg=f"Case {t['case_number']} closed. The contact's next message opens a new case.")


@router.post("/threads/{thread_id}/reopen")
def reopen_case(thread_id: int):
    t = db.get_thread(thread_id) or _404()
    s = db.latest_summary(thread_id)
    db.set_thread(thread_id, status="approved" if s and s["status"] == "approved" else
                  ("draft_ready" if s and s["status"] == "draft" else "open"))
    return _back(thread_id, msg=f"Case {t['case_number']} reopened.")


@router.post("/threads/{thread_id}/merge")
def merge_case(thread_id: int, into: str = Form(...)):
    target = db.get_thread_by_case(into)
    if not target:
        return _back(thread_id, error=f"No case numbered {into}.")
    if target["id"] == thread_id:
        return _back(thread_id, error="That is this case.")
    src = db.get_thread(thread_id) or _404()
    n = db.merge_threads(thread_id, target["id"])
    return _back(target["id"], msg=f"Moved {n} item(s) from {src['case_number']} into this case.")


@router.post("/items/{item_id}/move")
def move_item(item_id: int, into: str = Form(...)):
    item = db.get_item(item_id) or _404()
    target = db.get_thread_by_case(into)
    if not target:
        return _back(item["thread_id"], error=f"No case numbered {into}.")
    db.move_item(item_id, target["id"])
    return _back(item["thread_id"], msg=f"Moved I{item_id} to {target['case_number']}.")


def _404():
    raise HTTPException(404)


# ------------------------------------------------------------------ media + JSON API

@router.get("/media/{item_id}")
def media_file(item_id: int):
    item = db.get_item(item_id)
    if not item or not item["media_path"]:
        raise HTTPException(404)
    path = media.abs_path(item["media_path"])
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type=item["media_mime"] or None)


@router.get("/api/threads/{thread_id}")
def api_thread(thread_id: int):
    thread = db.get_thread(thread_id)
    if not thread:
        raise HTTPException(404)
    items = db.thread_items(thread_id)
    for it in items:
        it["extraction_meta"] = _meta(it)
    return {"thread": thread, "contact": db.get_contact(thread["contact_id"]), "items": items,
            "summary": db.latest_summary(thread_id)}


@router.get("/api/cases")
def api_cases(status: str = "approved"):
    """Hand-off point for the next stage (central database / priority matrix)."""
    out = []
    for t in db.list_threads():
        s = db.latest_summary(t["id"])
        if s and s["status"] == status:
            d = s["data"]
            follow = d.get("_follow_up") or {}
            out.append({"case_number": t["case_number"], "thread_id": t["id"], "contact": t["address"],
                        "reviewed_by": s["reviewer"], "reviewed_at": s["reviewed_at"],
                        "trust": (d.get("_trust") or {}).get("level", "self_reported"),
                        "profile_complete": follow.get("complete"),
                        "missing_topics": follow.get("missing_topics", []),
                        "case": d})
    return JSONResponse(out)
