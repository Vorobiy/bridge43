"""Twilio: WhatsApp messages, SMS, phone calls and WhatsApp calls - one webhook set.

Configure in the Twilio console:
  * WhatsApp sender / SMS number  -> "A message comes in":  POST {PUBLIC_BASE_URL}/webhooks/twilio/messaging
  * Voice number / WhatsApp sender voice config -> "A call comes in": POST {PUBLIC_BASE_URL}/webhooks/twilio/voice
Recording callbacks are wired up automatically by the TwiML this module returns.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from .. import consent, db, media
from ..config import settings
from ..processing import pipeline

log = logging.getLogger("bridge43.twilio")
router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])

VOICE_NOTICE_FR = ("Bonjour, vous êtes en ligne avec la Fondation PS43. Cet appel est enregistré et transcrit "
                   "pour aider votre école. Si vous ne souhaitez pas être enregistré, raccrochez et envoyez-nous un message.")
VOICE_NOTICE_EN = ("Hello, you have reached PS43 Foundation. This call is recorded and transcribed to help your school. "
                   "If you do not want to be recorded, please hang up and send us a message instead.")
VOICEMAIL_PROMPT_FR = "Après le bip, dites le nom de votre école, votre ville, et ce dont votre école a besoin."
VOICEMAIL_PROMPT_EN = "After the beep, please say your school's name, your town, and what your school needs."


# ------------------------------------------------------------------ security

def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


async def _verified_form(request: Request) -> dict[str, str]:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if settings.validate_twilio_signature and settings.twilio_auth_token:
        url = settings.public_base_url + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        expected = compute_signature(settings.twilio_auth_token, url, form)
        if not hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(status_code=403, detail="invalid Twilio signature")
    return form


def twiml(inner: str = "") -> Response:
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>',
                    media_type="application/xml")


# ------------------------------------------------------------------ media

def download(url: str) -> tuple[bytes, str]:
    auth = (settings.twilio_account_sid, settings.twilio_auth_token) if settings.twilio_enabled else None
    resp = httpx.get(url, auth=auth, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "application/octet-stream")


def fetch_media_then_process(item_id: int, url: str, filename_hint: str | None = None) -> None:
    item = db.get_item(item_id)
    if not item:
        return
    try:
        data, mime = download(url)
        rel = media.save_bytes(data, mime=item["media_mime"] or mime, filename=filename_hint)
        contact = db.get_contact(item["contact_id"])
        held = settings.require_explicit_consent and contact and contact["consent_status"] != "granted"
        db.update_item(item_id, media_path=rel, media_mime=item["media_mime"] or mime,
                       status="held" if held else "received")
    except Exception as exc:
        log.exception("media download failed for item %s", item_id)
        db.update_item(item_id, status="failed", error=f"media download failed: {exc}"[:1000])
        pipeline.thread_settled(item["thread_id"])
        return
    if not held:
        pipeline.process_item(item_id)


# ------------------------------------------------------------------ messages (WhatsApp + SMS)

@router.post("/messaging")
async def inbound_message(request: Request) -> Response:
    form = await _verified_form(request)
    sender = form.get("From", "")
    if not sender:
        raise HTTPException(400, "missing From")
    channel = "whatsapp" if sender.startswith("whatsapp:") else "sms"
    contact = db.get_or_create_contact(sender, channel, form.get("ProfileName") or None)
    body = (form.get("Body") or "").strip()
    num_media = int(form.get("NumMedia") or 0)
    sid = form.get("MessageSid") or form.get("SmsSid")

    decision = consent.decide(contact, body)
    if decision.deleted:
        return twiml(f"<Message>{escape(decision.reply)}</Message>")
    for released in decision.released_item_ids:
        pipeline.enqueue(released)

    # A bare YES / STOP reply is a consent answer, not case content.
    if not (num_media == 0 and consent.is_consent_keyword(body)):
        hold = decision.hold
        if num_media == 0 and body:
            item = db.add_item(contact_id=contact["id"], channel=channel, kind="text", body=body,
                               external_id=sid, status="held" if hold else "received",
                               author=form.get("ProfileName") or None)
            if item and not hold:
                pipeline.enqueue(item["id"])
        for i in range(num_media):
            url = form.get(f"MediaUrl{i}")
            mime = form.get(f"MediaContentType{i}") or None
            if not url:
                continue
            item = db.add_item(contact_id=contact["id"], channel=channel, kind=media.kind_for(mime),
                               body=body if i == 0 and body else None, media_mime=mime,
                               external_id=f"{sid}:{i}" if sid else None, status="awaiting_media",
                               author=form.get("ProfileName") or None)
            if item:
                pipeline.submit(fetch_media_then_process, item["id"], url)

    if decision.reply:
        return twiml(f"<Message>{escape(decision.reply)}</Message>")
    return twiml()


# ------------------------------------------------------------------ calls (phone + WhatsApp calls)

def _callback(path: str) -> str:
    return escape(f"{settings.public_base_url}/webhooks/twilio/{path}")


@router.post("/voice")
async def inbound_call(request: Request) -> Response:
    form = await _verified_form(request)
    caller = form.get("From", "") or "unknown"
    call_sid = form.get("CallSid", "")
    channel = "whatsapp" if caller.startswith("whatsapp:") else "call"
    contact = db.get_or_create_contact(caller, channel, form.get("CallerName") or None)
    if contact["consent_status"] in ("pending", "opted_out"):
        db.set_consent(contact["id"], "notified")  # the spoken notice below
    mode = "forwarded call" if settings.staff_forward_number else "recorded message"
    db.add_item(contact_id=contact["id"], channel="call", kind="audio",
                body=f"Inbound {'WhatsApp ' if channel == 'whatsapp' else ''}call ({mode}) from {caller}",
                external_id=call_sid or None, status="awaiting_media")

    notice = (f'<Say language="fr-FR">{escape(VOICE_NOTICE_FR)}</Say>'
              f'<Say language="en-US">{escape(VOICE_NOTICE_EN)}</Say>')
    rec_cb = _callback("recording")
    if settings.staff_forward_number:
        inner = (notice + f'<Dial record="record-from-answer-dual" recordingStatusCallback="{rec_cb}" '
                 f'recordingStatusCallbackEvent="completed">{escape(settings.staff_forward_number)}</Dial>')
    else:
        inner = (notice + f'<Say language="fr-FR">{escape(VOICEMAIL_PROMPT_FR)}</Say>'
                 f'<Say language="en-US">{escape(VOICEMAIL_PROMPT_EN)}</Say>'
                 f'<Record maxLength="{settings.max_voicemail_seconds}" playBeep="true" '
                 f'recordingStatusCallback="{rec_cb}" recordingStatusCallbackEvent="completed" '
                 f'action="{_callback("voice/done")}"/>')
    return twiml(inner)


@router.post("/voice/done")
async def call_done(request: Request) -> Response:
    await _verified_form(request)
    return twiml('<Say language="fr-FR">Merci. Au revoir.</Say><Say language="en-US">Thank you. Goodbye.</Say><Hangup/>')


@router.post("/recording")
async def recording_ready(request: Request) -> Response:
    form = await _verified_form(request)
    if form.get("RecordingStatus", "completed") != "completed":
        return Response(status_code=204)
    call_sid, rec_url = form.get("CallSid", ""), form.get("RecordingUrl", "")
    if not rec_url:
        raise HTTPException(400, "missing RecordingUrl")
    item = db.get_item_by_external_id(call_sid) if call_sid else None
    if item is None or item["media_path"]:
        # Unknown call, or a second recording on the same call: store it as its own item.
        parent = item
        contact_id = parent["contact_id"] if parent else db.get_or_create_contact(f"call:{call_sid}", "call")["id"]
        item = db.add_item(contact_id=contact_id, channel="call", kind="audio", body="Call recording",
                           external_id=form.get("RecordingSid") or None, status="awaiting_media")
        if item is None:
            return Response(status_code=204)  # duplicate callback
    db.update_item(item["id"], media_mime="audio/mpeg",
                   extraction_meta={"duration_reported": form.get("RecordingDuration")})
    pipeline.submit(fetch_media_then_process, item["id"], rec_url + ".mp3", "call.mp3")
    return Response(status_code=204)


# ------------------------------------------------------------------ outbound

class SendError(RuntimeError):
    pass


def send_message(to_address: str, text: str) -> str:
    """Send a WhatsApp or SMS message through Twilio. Returns the Twilio message SID."""
    if not settings.twilio_enabled:
        raise SendError("Twilio is not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)")
    if to_address.startswith("whatsapp:"):
        sender = settings.twilio_whatsapp_from
    elif to_address.startswith("+"):
        sender = settings.twilio_sms_from
    else:
        raise SendError(f"cannot message {to_address}: not a WhatsApp or phone address")
    if not sender:
        raise SendError("no Twilio sender configured for this channel (TWILIO_WHATSAPP_FROM / TWILIO_SMS_FROM)")
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={"From": sender, "To": to_address, "Body": text[:1500]},
        timeout=30,
    )
    if resp.status_code >= 400:
        # 63016 = outside WhatsApp's 24h window: needs an approved template instead.
        raise SendError(f"Twilio {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("sid", "")
