"""KoBoToolbox: offline field forms filled during school visits.

In KoBoToolbox: Project > Settings > REST Services > add
    {PUBLIC_BASE_URL}/webhooks/kobo?secret={KOBO_WEBHOOK_SECRET}
Each submission becomes one "form" item (plus one item per attached photo/audio).
"""
from __future__ import annotations

import hmac
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from .. import db, media
from ..config import settings
from ..processing import pipeline

log = logging.getLogger("bridge43.kobo")
router = APIRouter(prefix="/webhooks", tags=["kobo"])

SKIP_PREFIXES = ("_", "meta/", "formhub/", "__version__")


def _short(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def flatten(sub: dict[str, Any]) -> str:
    """Render a submission as readable 'field: value' lines (repeat groups included)."""
    lines: list[str] = []

    def walk(d: dict[str, Any], indent: str = "") -> None:
        for k, v in d.items():
            if k.startswith(SKIP_PREFIXES) or v in (None, "", []):
                continue
            label = _short(k).replace("_", " ")
            if isinstance(v, list) and v and isinstance(v[0], dict):
                for n, entry in enumerate(v, 1):
                    lines.append(f"{indent}{label} #{n}:")
                    walk(entry, indent + "  ")
            elif isinstance(v, dict):
                lines.append(f"{indent}{label}:")
                walk(v, indent + "  ")
            else:
                lines.append(f"{indent}{label}: {v}")

    walk(sub)
    return "\n".join(lines)


def contact_address(sub: dict[str, Any]) -> tuple[str, str | None]:
    fields = {_short(k): v for k, v in sub.items() if isinstance(v, (str, int))}
    name = next((str(fields[f]) for f in ("school_name", "name", "organization") if fields.get(f)), None)
    for f in settings.kobo_contact_fields:
        val = fields.get(f)
        if val:
            val = str(val).strip()
            if val.startswith("+") or val.replace(" ", "").isdigit():
                return "+" + val.lstrip("+").replace(" ", ""), name   # a phone number: same contact as SMS
            return f"kobo:{val.lower()}", name
    return f"kobo:{sub.get('_uuid') or sub.get('_id') or 'unknown'}", name


def _download(url: str) -> tuple[bytes, str]:
    headers = {"Authorization": f"Token {settings.kobo_api_token}"} if settings.kobo_api_token else {}
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "application/octet-stream")


def _fetch_attachment(item_id: int, url: str, filename: str | None) -> None:
    item = db.get_item(item_id)
    try:
        data, mime = _download(url)
        rel = media.save_bytes(data, mime=item["media_mime"] or mime, filename=filename)
        db.update_item(item_id, media_path=rel, media_mime=item["media_mime"] or mime, status="received")
    except Exception as exc:
        log.exception("kobo attachment download failed")
        db.update_item(item_id, status="failed", error=f"attachment download failed: {exc}"[:1000])
        pipeline.thread_settled(item["thread_id"])
        return
    pipeline.process_item(item_id)


@router.post("/kobo")
async def kobo_submission(request: Request, secret: str = "") -> dict:
    if settings.kobo_webhook_secret:
        given = secret or request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(given, settings.kobo_webhook_secret):
            raise HTTPException(403, "bad secret")
    sub = await request.json()
    if not isinstance(sub, dict):
        raise HTTPException(400, "expected a JSON object")
    address, name = contact_address(sub)
    contact = db.get_or_create_contact(address, "kobo", name)
    db.set_consent(contact["id"], "granted")  # consent is collected in person by the field agent
    uuid = sub.get("_uuid") or sub.get("meta/instanceID") or None
    sent_at = sub.get("_submission_time")
    if sent_at and "+" not in sent_at and not sent_at.endswith("Z"):
        sent_at += "+00:00"
    form_item = db.add_item(contact_id=contact["id"], channel="kobo", kind="form",
                            body=f"Field visit form ({sub.get('_xform_id_string', 'KoBo')})\n" + flatten(sub),
                            external_id=f"kobo:{uuid}" if uuid else None, author=sub.get("_submitted_by"),
                            sent_at=sent_at)
    if form_item is None:
        return {"status": "duplicate"}
    pipeline.enqueue(form_item["id"])
    queued = 0
    for att in sub.get("_attachments") or []:
        url = att.get("download_url") or att.get("download_large_url")
        if not url:
            continue
        mime = att.get("mimetype")
        fname = (att.get("filename") or "").rsplit("/", 1)[-1] or None
        item = db.add_item(contact_id=contact["id"], channel="kobo", kind=media.kind_for(mime, fname),
                           media_mime=mime, external_id=f"kobo:{uuid}:{fname}" if uuid else None,
                           status="awaiting_media", thread_id=form_item["thread_id"], sent_at=sent_at)
        if item:
            pipeline.submit(_fetch_attachment, item["id"], url, fname)
            queued += 1
    return {"status": "ok", "thread_id": form_item["thread_id"], "attachments": queued}
