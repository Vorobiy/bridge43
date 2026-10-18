"""Bridge43 intake server: run with `uvicorn app.main:app --reload`."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db, ops_web, public_web, recorder_api, web
from .channels import kobo
from .config import settings
from .processing import ffmpeg, pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bridge43")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    seed_demo_if_empty()
    from . import workflow
    changed = workflow.refresh_all_cases()
    if changed:
        log.info("%d case(s) brought up to date (resolved when every need is confirmed)", changed)
    requeued = pipeline.process_pending()
    if requeued:
        log.info("re-queued %d unfinished items", requeued)
    for problem in config_problems():
        log.warning("config: %s", problem)
    yield


def seed_demo_if_empty() -> bool:
    """SEED_DEMO=true: load the made-up demo cases when the database is empty (e.g. a fresh Railway deploy),
    so the hosted inbox shows the same demo as a laptop that ran scripts/seed_demo.py. Never touches real cases."""
    if os.getenv("SEED_DEMO", "").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    if db.list_threads():
        return False
    try:
        from scripts import seed_demo
        seed_demo.main()
        log.info("database was empty: demo data loaded (SEED_DEMO)")
        return True
    except Exception:
        log.exception("could not load the demo data")
        return False


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
    return problems


app = FastAPI(title="Bridge43 intake", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")  # PS43 logos, fonts
app.include_router(kobo.router)
app.include_router(recorder_api.router)
app.include_router(public_web.router)
app.include_router(web.router)
app.include_router(ops_web.router)


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "public_url": settings.public_base_url, "warnings": config_problems()}
