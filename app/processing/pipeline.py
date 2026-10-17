"""The conversion step: every intake item becomes text the AI summary can read.

    text      -> kept as is
    audio     -> Whisper transcript (with [mm:ss] timestamps for calls)
    image     -> Claude vision description
    video     -> soundtrack transcript + description of 4 still frames
    document  -> PDF summary (Claude) or plain text
    form      -> already text (KoBo / agent form)

Items are processed in a small background pool. When a thread has no items left in flight,
its AI case draft is (re)generated after a short debounce, so a burst of WhatsApp messages
produces one draft, not ten.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import db, media
from ..config import settings
from . import ffmpeg, transcribe, vision

log = logging.getLogger("bridge43.pipeline")

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="convert")
_timers: dict[int, threading.Timer] = {}
_timers_lock = threading.Lock()
SUMMARY_DEBOUNCE_SECONDS = 20.0


def extract(item: dict) -> tuple[str, dict]:
    """Return (text, meta) for one item. Raises on failure."""
    kind = item["kind"]
    if kind in ("text", "form"):
        return (item["body"] or "").strip(), {}

    if not item["media_path"]:
        return (item["body"] or "").strip(), {"note": "no media attached"}
    path = media.abs_path(item["media_path"])
    mime = (item["media_mime"] or media.guess_mime(path.name)).split(";")[0].strip()
    caption = (item["body"] or "").strip() or None

    if kind == "audio":
        t = transcribe.transcribe(path)
        text = transcribe.format_with_timestamps(t) if item["channel"] == "call" else t.text
        return text, {"language": t.language, "duration": t.duration, "model": t.model}

    if kind == "image":
        desc = vision.describe_images([(path, mime)], what="photo", caption=caption)
        return desc, {"model": settings.vision_model}

    if kind == "video":
        parts, meta = [], {"model": settings.vision_model}
        t = transcribe.transcribe(path)
        if t.text:
            parts.append(f"Spoken in the video: {t.text}")
            meta.update(language=t.language, duration=t.duration, transcribe_model=t.model)
        with ffmpeg.workdir() as tmp:
            frames = ffmpeg.video_frames(path, Path(tmp), count=4)
            if frames:
                desc = vision.describe_images([(f, "image/jpeg") for f in frames],
                                              what="set of still frames from one short video", caption=caption)
                parts.append(f"What the video shows: {desc}")
                meta["frames"] = len(frames)
        return "\n".join(parts), meta

    if kind == "document":
        if mime == "application/pdf" or path.suffix.lower() == ".pdf":
            return vision.describe_pdf(path), {"model": settings.vision_model}
        if mime.startswith("text/") or path.suffix.lower() in (".txt", ".csv"):
            return path.read_text(errors="replace")[:20000], {}
        return "", {"note": f"unsupported document type {mime}; staff should open the file"}

    return (item["body"] or "").strip(), {"note": f"unknown kind {kind}"}


def process_item(item_id: int) -> None:
    item = db.get_item(item_id)
    if not item or item["status"] not in ("received", "failed", "processing"):
        return
    db.update_item(item_id, status="processing", error=None)
    try:
        text, meta = extract(item)
        db.update_item(item_id, status="processed", extracted_text=text, extraction_meta=meta)
        lang = meta.get("language")
        if lang and item["direction"] == "in":
            contact = db.get_contact(item["contact_id"])
            if contact and not contact["language"]:
                db.update_contact(contact["id"], language=transcribe.LANG_NAMES.get(lang, lang))
    except Exception as exc:  # keep the item, show the error to staff, allow retry
        log.exception("processing item %s failed", item_id)
        db.update_item(item_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000])
    thread_settled(item["thread_id"])


def thread_settled(thread_id: int) -> None:
    """Called after an item finishes; schedules a fresh AI draft once nothing is in flight."""
    if not settings.auto_summarize:
        return
    items = db.thread_items(thread_id)
    if any(i["status"] in ("received", "processing", "awaiting_media") for i in items):
        return
    schedule_summary(thread_id)


def schedule_summary(thread_id: int, delay: float | None = None) -> None:
    from ..summary import summarize_thread  # local import: summary depends on pipeline helpers

    delay = SUMMARY_DEBOUNCE_SECONDS if delay is None else delay

    def run() -> None:
        with _timers_lock:
            _timers.pop(thread_id, None)
        try:
            summarize_thread(thread_id)
        except Exception:
            log.exception("summary for thread %s failed", thread_id)

    with _timers_lock:
        old = _timers.pop(thread_id, None)
        if old:
            old.cancel()
        t = threading.Timer(delay, run)
        t.daemon = True
        _timers[thread_id] = t
        t.start()


def enqueue(item_id: int) -> None:
    _pool.submit(process_item, item_id)


def submit(fn, *args) -> None:
    """Run any background job (e.g. a media download) on the conversion pool."""
    _pool.submit(fn, *args)


def process_pending() -> int:
    """Re-queue anything not finished (e.g. after a restart). Returns how many were queued."""
    pending = db.items_with_status("received", "processing")
    for item in pending:
        if item["status"] == "processing":
            db.update_item(item["id"], status="received")
        enqueue(item["id"])
    return len(pending)
