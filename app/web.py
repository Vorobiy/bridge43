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

from . import db, media
from .channels import staff_intake, twilio
from .config import settings
from .processing import pipeline
from .summary import NEED_CATEGORIES, READINESS_KEYS, normalize, summarize_thread

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_basic = HTTPBasic(auto_error=False)


def staff(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    if not settings.staff_password:
        return "staff"  # open in local development; set STAFF_PASSWORD before going online
    ok = credentials is not None and secrets.compare_digest(credentials.username, settings.staff_username) \
        and secrets.compare_digest(credentials.password, settings.staff_password)
    if not ok:
        raise HTTPException(401, "login required", headers={"WWW-Authenticate": 'Basic realm="Bridge43"'})
    return credentials.username


router = APIRouter(dependencies=[Depends(staff)])


def _ctx(request: Request, **kw) -> dict:
    return {"request": request, "settings": settings, "open_mode": not settings.staff_password, **kw}


def _meta(item: dict) -> dict:
    return json.loads(item["extraction_meta"]) if item.get("extraction_meta") else {}


# ------------------------------------------------------------------ inbox

@router.get("/", response_class=HTMLResponse)
def inbox(request: Request, status: str | None = None):
    threads = db.list_threads(status)
    counts = {s: sum(1 for t in db.list_threads() if t["status"] == s) for s in ("open", "draft_ready", "approved", "rejected")}
    return templates.TemplateResponse(request, "inbox.html", _ctx(request, threads=threads, status=status, counts=counts))


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
    in_flight = sum(1 for i in items if i["status"] in ("received", "processing", "awaiting_media"))
    new_since = []
    if summary:
        used = set(summary["item_ids"])
        new_since = [i["id"] for i in items if i["id"] not in used and i["status"] in ("processed", "failed")]
    return templates.TemplateResponse(request, "thread.html", _ctx(
        request, thread=thread, contact=contact, items=items, summary=summary, in_flight=in_flight,
        new_since=new_since, msg=msg, error=error, need_categories=NEED_CATEGORIES, readiness_keys=READINESS_KEYS,
        summary_json=json.dumps(summary["data"], indent=2, ensure_ascii=False) if summary else "",
        twilio_enabled=settings.twilio_enabled, can_message=contact["address"].startswith(("whatsapp:", "+"))))


def _back(thread_id: int, **q: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(f"/threads/{thread_id}" + (f"?{urlencode(q)}" if q else ""), status_code=303)


@router.post("/threads/{thread_id}/summarize")
async def summarize_now(thread_id: int):
    try:
        result = await run_in_threadpool(summarize_thread, thread_id)
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
    parsed = normalize(parsed)
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
    case_number = db.next_case_number()
    db.update_summary(summary_id, status="approved", reviewer=who, review_notes=notes.strip() or None,
                      reviewed_at=db.now(), case_number=case_number)
    db.set_thread(s["thread_id"], status="approved")
    return _back(s["thread_id"], msg=f"Approved as case {case_number}.")


@router.post("/summaries/{summary_id}/reject")
def reject(summary_id: int, notes: str = Form(""), who: str = Depends(staff)):
    s = _summary_for(summary_id)
    if s["status"] != "draft":
        return _back(s["thread_id"], error="This draft was already reviewed.")
    db.update_summary(summary_id, status="rejected", reviewer=who, review_notes=notes.strip() or None,
                      reviewed_at=db.now())
    db.set_thread(s["thread_id"], status="rejected")
    return _back(s["thread_id"], msg="Rejected. New messages from this contact will start a fresh thread.")


@router.post("/summaries/{summary_id}/send-confirmation")
async def send_confirmation(summary_id: int, text: str = Form(...), who: str = Depends(staff)):
    s = _summary_for(summary_id)
    thread = db.get_thread(s["thread_id"])
    contact = db.get_contact(thread["contact_id"])
    if contact["consent_status"] == "opted_out":
        return _back(thread["id"], error="This contact opted out.")
    try:
        sid = await run_in_threadpool(twilio.send_message, contact["address"], text.strip())
    except twilio.SendError as exc:
        return _back(thread["id"], error=f"Not sent: {exc}")
    db.add_item(contact_id=contact["id"], channel="whatsapp" if contact["address"].startswith("whatsapp:") else "sms",
                kind="text", direction="out", author=who, body=f"[Confirmation sent] {text.strip()}",
                external_id=sid or None, status="processed", thread_id=thread["id"])
    db.update_summary(summary_id, confirmation_sent_at=db.now())
    return _back(thread["id"], msg="Confirmation sent. The contact's reply will appear in this thread.")


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
            out.append({"case_number": s["case_number"], "thread_id": t["id"], "contact": t["address"],
                        "reviewed_by": s["reviewer"], "reviewed_at": s["reviewed_at"], "case": s["data"]})
    return JSONResponse(out)
