"""Bridge43 intake server: run with `uvicorn app.main:app --reload`."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db, ops_web, public_web, recorder_api, web
from .channels import kobo, twilio
from .config import settings
from .processing import ffmpeg, pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridge43")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    requeued = pipeline.process_pending()
    if requeued:
        log.info("re-queued %d unfinished items", requeued)
    for problem in config_problems():
        log.warning("config: %s", problem)
    yield


def config_problems() -> list[str]:
    problems = []
    if not settings.anthropic_api_key:
        problems.append("ANTHROPIC_API_KEY missing: photos, videos, PDFs and AI drafts will fail")
    if settings.transcribe_provider != "local" and not settings.transcribe_api_key:
        problems.append("TRANSCRIBE_API_KEY missing: voice notes and calls will fail (or set TRANSCRIBE_PROVIDER=local)")
    if not ffmpeg.available():
        problems.append("ffmpeg not found: audio and video cannot be processed")
    if not settings.staff_password:
        problems.append("STAFF_PASSWORD not set: the staff screens are open to anyone who can reach this server")
    if settings.hosted and not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        problems.append("no persistent volume attached: cases are lost on every redeploy (Railway: Attach Volume at /data)")
    if "localhost" in settings.public_base_url and settings.hosted:
        problems.append("PUBLIC_BASE_URL points to localhost: Twilio cannot reach this server")
    if not settings.twilio_enabled:
        problems.append("Twilio not configured: live WhatsApp/SMS/calls and confirmations are off (uploads still work)")
    return problems


app = FastAPI(title="Bridge43 intake", lifespan=lifespan)
app.include_router(twilio.router)
app.include_router(kobo.router)
app.include_router(recorder_api.router)
app.include_router(public_web.router)
app.include_router(web.router)
app.include_router(ops_web.router)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "public_url": settings.public_base_url, "warnings": config_problems()}
