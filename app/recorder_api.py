"""Upload endpoint for the Bridge43 Mac call recorder (see recorder/).

The recorder runs on a PS43 staff laptop, records WhatsApp Desktop calls, and posts each
recording here with the caller's name or number. Protected by RECORDER_TOKEN, not the staff
login, so the helper can run unattended.
"""
from __future__ import annotations

import hmac
import re

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .channels import staff_intake
from .config import settings

router = APIRouter(prefix="/api/recorder", tags=["recorder"])

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
