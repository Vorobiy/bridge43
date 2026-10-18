"""Upload endpoint for the Bridge43 Mac call recorder (see recorder/).

The recorder runs on a PS43 staff laptop, records WhatsApp Desktop calls, and posts each
recording here with the caller's name or number. Protected by RECORDER_TOKEN, not the staff
login, so the helper can run unattended.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .channels import staff_intake
from .config import settings

router = APIRouter(prefix="/api/recorder", tags=["recorder"])
log = logging.getLogger("bridge43.recorder")

MAX_BYTES = 200 * 1024 * 1024


def _check(authorization: str | None) -> None:
    if not settings.recorder_token:
        raise HTTPException(403, "recorder uploads are off: set RECORDER_TOKEN on the server")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, settings.recorder_token):
        raise HTTPException(401, "bad recorder token")


def split_contact(contact: str) -> tuple[str | None, str | None]:
    """'Mr Fon +237 6 70 00 00 01' -> ('Mr Fon', '+237670000001'); a bare number -> (None, number)."""
    from .db import clean_name
    contact = clean_name(contact) or ""
    m = re.search(r"\+?\d[\d\s().-]{6,}\d", contact)
    phone = re.sub(r"[^\d+]", "", m.group(0)) if m else None
    name = (contact.replace(m.group(0), "") if m else contact).strip(" -–,:") or None
    return name, phone


@router.get("/recent")
def recent_cases(limit: int = 8, authorization: str | None = Header(default=None)) -> list[dict]:
    """Latest cases for the Mac menu bar app (not-relevant conversations left out)."""
    _check(authorization)
    from . import db
    out = []
    for t in db.list_threads():
        if t["status"] == "not_relevant":
            continue
        out.append({"case_number": t["case_number"], "name": t["display_name"] or t["address"],
                    "status": t["status"], "title": t["title"], "item_count": t["item_count"],
                    "last_item_at": t["last_item_at"], "url": f"{settings.public_base_url}/threads/{t['id']}"})
        if len(out) >= max(1, min(limit, 50)):
            break
    return out


@router.get("/ping")
def ping(authorization: str | None = Header(default=None)) -> dict:
    _check(authorization)
    return {"ok": True}


@router.post("/calls")
async def upload_call(
    file: UploadFile = File(...),
    contact: str = Form(""),
    started_at: str = Form(""),
    duration_seconds: float = Form(0),
    source: str = Form("whatsapp_call"),
    authorization: str | None = Header(default=None),
) -> dict:
    _check(authorization)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty recording")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "recording too large")
    name, phone = split_contact(contact)
    minutes = f"{duration_seconds / 60:.1f} min" if duration_seconds else "unknown length"
    notes = (f"Call recorded automatically by the Bridge43 call recorder on a PS43 laptop "
             f"(started {started_at or 'unknown'}, {minutes}). The recorder reminded staff to tell the caller the call is recorded.")
    result = await run_in_threadpool(
        staff_intake.add_staff_notes,
        contact_name=name or (phone if not name else None) or "Unknown caller",
        contact_phone=phone,
        source=source if source in ("whatsapp_call", "phone_call") else "whatsapp_call",
        notes=notes,
        files=[staff_intake.UploadedFile(filename=file.filename or "call.m4a", data=data,
                                         content_type=file.content_type)],
        staff_name="Call recorder",
    )
    from . import db
    return {"ok": True, "thread_id": result["thread_id"],
            "case_number": (db.get_thread(result["thread_id"]) or {}).get("case_number"),
            "url": f"{settings.public_base_url}/threads/{result['thread_id']}"}


# ------------------------------------------------------------ WhatsApp Desktop messages

def _existing_contact(address: str, name: str | None) -> dict | None:
    """The contact this chat belongs to, without creating one."""
    from . import db
    canon = db.canonical_address(address)
    name = db.clean_name(name)
    with db.tx() as conn:
        found = conn.execute("SELECT * FROM contacts WHERE address = ?", (canon,)).fetchone()
        if not found and name:
            # someone we only knew by name (e.g. their WhatsApp call came in first)
            found = conn.execute(
                "SELECT * FROM contacts WHERE lower(display_name) = lower(?) AND address NOT LIKE '+%' "
                "ORDER BY updated_at DESC LIMIT 1", (name.strip(),)).fetchone()
    return dict(found) if found else None


def _has_active_case(contact_id: int) -> bool:
    from . import db
    with db.tx() as conn:
        return bool(conn.execute(
            f"SELECT 1 FROM threads WHERE contact_id = ? AND status IN ({','.join('?' * len(db.ACTIVE_STATUSES))})",
            (contact_id, *db.ACTIVE_STATUSES)).fetchone())


def _triage_text(name: str | None, messages: list[dict], files: dict[str, bytes]) -> str:
    """The conversation as text for screening; voice notes are transcribed so a voice-only chat counts."""
    from .processing import transcribe
    lines = []
    for m in messages:
        who = "Staff" if m.get("from_me") else (name or "Contact")
        text = (m.get("text") or "").strip()
        f = m.get("file")
        if m.get("kind") == "audio" and f in files:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / Path(f).name
                    path.write_bytes(files[f])
                    text = (text + " [voice note] " + transcribe.transcribe(path).text).strip()
            except Exception as exc:  # screening still works on the texts
                log.warning("could not transcribe voice note for screening: %s", exc)
        elif m.get("kind") not in (None, "text") and not text:
            text = f"[{m.get('kind')}]"
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def _iso(at: str | None) -> str | None:
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat((at or "").replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None


def file_whatsapp_messages(payload: dict, files: dict[str, bytes], avatar: bytes | None = None) -> dict:
    from . import db, media, triage
    from .processing import pipeline

    chat_id = str(payload.get("chat_id") or "").strip()
    name = db.clean_name(payload.get("name")) or None
    phone = re.sub(r"[^\d+]", "", payload.get("phone") or "")
    if phone and not phone.startswith("+"):
        phone = "+" + phone
    if not chat_id and not phone:
        raise HTTPException(400, "chat_id or phone required")
    address = f"whatsapp:{phone}" if phone else f"wadesktop:{chat_id}"
    new = [m for m in payload.get("messages") or [] if m.get("id") is not None]
    context = [m for m in payload.get("context") or [] if m.get("id") is not None]
    if not new:
        return {"filed": False, "reason": "no messages"}

    contact = _existing_contact(address, name)
    from . import consent
    if contact and any(not m.get("from_me") and consent.is_opt_out(m.get("text")) for m in new):
        consent.opt_out(contact["id"])
        return {"filed": False, "reason": "contact asked to stop: their data was deleted"}
    if contact and contact.get("consent_status") == "opted_out":
        if not any(not m.get("from_me") for m in new):
            return {"filed": False, "reason": "contact opted out"}
        db.set_consent(contact["id"], "pending")  # they wrote again after opting out: a fresh start
    to_file = new
    if not (contact and _has_active_case(contact["id"])):
        relevant, reason = triage.is_relevant(_triage_text(name, context + new, files))
        if not relevant:
            return {"filed": False, "reason": reason}
        to_file = context + new   # the conversation so far belongs in the new case

    contact = db.get_or_create_contact(address, "whatsapp", name)
    if avatar:  # their WhatsApp profile photo, shown on the case and in the inbox
        with db.tx() as conn:
            conn.execute("UPDATE contacts SET avatar_path = ? WHERE id = ?",
                         (media.save_bytes(avatar, mime="image/jpeg", filename="avatar.jpg"), contact["id"]))
    thread = db.open_thread_for(contact["id"])
    created = []
    for m in sorted(to_file, key=lambda m: m.get("at") or ""):
        common = dict(contact_id=contact["id"], channel="whatsapp", thread_id=thread["id"],
                      direction="out" if m.get("from_me") else "in",
                      author="PS43 staff (WhatsApp)" if m.get("from_me") else name,
                      sent_at=_iso(m.get("at")), external_id=f"wadesktop:{chat_id or phone}:{m['id']}")
        text = (m.get("text") or "").strip() or None
        f = m.get("file")
        if f and f in files and files[f]:
            mime = media.guess_mime(f)
            rel = media.save_bytes(files[f], mime=mime, filename=f)
            item = db.add_item(kind=media.kind_for(mime, f), body=text, media_path=rel, media_mime=mime, **common)
        elif m.get("kind") not in (None, "text"):
            item = db.add_item(kind="text", body=(text + " " if text else "") +
                               f"[{m.get('kind')} not downloaded on the PS43 laptop]", **common)
        elif text:
            item = db.add_item(kind="text", body=text, **common)
        else:
            continue
        if item:
            created.append(item["id"])
    for item_id in created:
        pipeline.enqueue(item_id)
    thread = db.get_thread(thread["id"]) or thread
    return {"filed": True, "created": len(created), "case_number": thread.get("case_number"),
            "url": f"{settings.public_base_url}/threads/{thread['id']}"}


@router.post("/messages")
async def upload_messages(
    chat: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    avatar: UploadFile | None = File(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """New messages from one WhatsApp Desktop chat, sent by the Mac menu bar app.

    chat = JSON {chat_id, phone, name, messages: [{id, at, from_me, kind, text, file}], context: [...]}
    A chat that already has a case is added to it; a new chat is screened first and stored only if
    it is about schools or logistics.
    """
    _check(authorization)
    try:
        payload = json.loads(chat)
    except ValueError:
        raise HTTPException(400, "chat must be JSON")
    blobs: dict[str, bytes] = {}
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > MAX_BYTES:
            raise HTTPException(413, "attachments too large")
        blobs[f.filename or ""] = data
    pic = await avatar.read() if avatar else None
    return await run_in_threadpool(file_whatsapp_messages, payload, blobs, (pic or None) if pic and len(pic) < 2_000_000 else None)
