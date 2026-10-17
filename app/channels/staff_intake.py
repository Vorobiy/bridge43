"""Intake that staff bring in by hand: exported WhatsApp chats, call recordings from a laptop
(e.g. Wispr Notetaker, OBS, Audacity), pasted transcripts, and notes from phone calls."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import db, media
from ..processing import pipeline
from . import whatsapp_export


@dataclass
class UploadedFile:
    filename: str
    data: bytes
    content_type: str | None = None


def contact_address(name: str | None, phone: str | None) -> tuple[str, str]:
    """Phone numbers become the same contact as their WhatsApp/SMS messages."""
    phone = re.sub(r"[^\d+]", "", phone or "")
    if phone:
        if not phone.startswith("+"):
            phone = "+" + phone
        return f"whatsapp:{phone}", "upload"
    from ..db import clean_name
    slug = re.sub(r"[^a-z0-9]+", "-", (clean_name(name) or "unknown").lower()).strip("-") or "unknown"
    return f"manual:{slug}", "upload"


def import_whatsapp_export(*, upload: UploadedFile, contact_name: str | None, contact_phone: str | None,
                           staff_names: list[str], day_first: bool | None = None) -> dict:
    parsed = whatsapp_export.parse_upload(upload.filename, upload.data, day_first)
    if not parsed.messages:
        raise ValueError("no messages found - is this a WhatsApp 'Export chat' file?")
    staff = {s.strip().lower() for s in staff_names if s.strip()}
    if not contact_name:
        others = [a for a in parsed.authors if a.lower() not in staff]
        contact_name = others[0] if others else None
    address, channel = contact_address(contact_name, contact_phone)
    contact = db.get_or_create_contact(address, channel, contact_name)
    thread = db.open_thread_for(contact["id"])

    created, duplicates, missing_media = [], 0, 0
    for msg in parsed.messages:
        direction = "out" if msg.author.lower() in staff else "in"
        common = dict(contact_id=contact["id"], channel="whatsapp", direction=direction, author=msg.author,
                      sent_at=msg.sent_at.isoformat(timespec="seconds"), thread_id=thread["id"],
                      external_id=whatsapp_export.message_key(address, msg))
        if msg.attachment:
            data = parsed.files.get(msg.attachment)
            mime = media.guess_mime(msg.attachment)
            if data is None:
                missing_media += 1
                item = db.add_item(kind="text", body=(msg.text + " " if msg.text else "") +
                                   f"[attachment {msg.attachment} not included in the upload]", **common)
            else:
                rel = media.save_bytes(data, mime=mime, filename=msg.attachment)
                item = db.add_item(kind=media.kind_for(mime, msg.attachment), body=msg.text or None,
                                   media_path=rel, media_mime=mime, **common)
        elif msg.media_omitted:
            missing_media += 1
            item = db.add_item(kind="text", body="[media not included in the export]", **common)
        else:
            item = db.add_item(kind="text", body=msg.text, **common)
        if item is None:
            duplicates += 1
        else:
            created.append(item["id"])
    for item_id in created:
        pipeline.enqueue(item_id)
    return {"thread_id": thread["id"], "contact_id": contact["id"], "created": len(created),
            "duplicates": duplicates, "missing_media": missing_media, "authors": parsed.authors}


def add_staff_notes(*, contact_name: str | None, contact_phone: str | None, source: str,
                    notes: str | None, files: list[UploadedFile], staff_name: str | None = None) -> dict:
    """Phone-call notes, a pasted transcript (e.g. from Wispr Notetaker) and/or recordings and photos.

    source: 'phone_call' | 'whatsapp_call' | 'visit' | 'other'
    """
    if not (notes and notes.strip()) and not files:
        raise ValueError("add notes or at least one file")
    address, channel = contact_address(contact_name, contact_phone)
    contact = db.get_or_create_contact(address, channel, contact_name)
    thread = db.open_thread_for(contact["id"])
    label = {"phone_call": "Phone call", "whatsapp_call": "WhatsApp call", "visit": "Site visit"}.get(source, "Staff")
    created = []
    if notes and notes.strip():
        item = db.add_item(contact_id=contact["id"], channel="agent", kind="text", direction="out",
                           author=staff_name or "PS43 staff", thread_id=thread["id"],
                           body=f"{label} notes / transcript recorded by PS43 staff:\n{notes.strip()}")
        created.append(item["id"])
    for f in files:
        if not f.data:
            continue
        mime = f.content_type if f.content_type and f.content_type != "application/octet-stream" else media.guess_mime(f.filename)
        rel = media.save_bytes(f.data, mime=mime, filename=f.filename)
        kind = media.kind_for(mime, f.filename)
        item = db.add_item(contact_id=contact["id"], channel="call" if kind == "audio" and "call" in source else "upload",
                           kind=kind, body=f"{label}: {f.filename}", media_path=rel, media_mime=mime,
                           thread_id=thread["id"], author=contact_name)
        created.append(item["id"])
    for item_id in created:
        pipeline.enqueue(item_id)
    return {"thread_id": thread["id"], "contact_id": contact["id"], "created": len(created)}
