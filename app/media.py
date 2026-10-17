"""Saving and classifying media files."""
from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path

from .config import settings

AUDIO_EXT = {".opus", ".ogg", ".oga", ".mp3", ".m4a", ".aac", ".wav", ".amr", ".webm", ".flac", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".3gpp", ".mkv", ".avi", ".m4v"}
DOC_EXT = {".pdf", ".docx", ".doc", ".txt", ".xlsx", ".csv"}


def kind_for(mime: str | None, filename: str | None = None) -> str:
    """Map a MIME type / file name to an item kind: audio, image, video, document."""
    mime = (mime or "").lower()
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        # WhatsApp sometimes labels voice notes as video/ogg-ish; trust the extension when it says audio
        if filename and Path(filename).suffix.lower() in AUDIO_EXT:
            return "audio"
        return "video"
    ext = Path(filename or "").suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return "document"


def guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    overrides = {".opus": "audio/ogg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".amr": "audio/amr",
                 ".webp": "image/webp", ".heic": "image/heic", ".3gp": "video/3gpp"}
    return overrides.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _ext_for(mime: str | None, filename: str | None) -> str:
    if filename and Path(filename).suffix:
        return Path(filename).suffix.lower()
    mime = (mime or "").split(";")[0].strip().lower()
    special = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/amr": ".amr", "image/jpeg": ".jpg",
               "video/mp4": ".mp4", "audio/mp4": ".m4a", "application/pdf": ".pdf"}
    return special.get(mime) or mimetypes.guess_extension(mime) or ".bin"


def save_bytes(data: bytes, *, mime: str | None = None, filename: str | None = None) -> str:
    """Write bytes under DATA_DIR/media and return the path relative to DATA_DIR."""
    settings.ensure_dirs()
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem)[:40] if filename else "media"
    name = f"{uuid.uuid4().hex[:12]}_{safe_stem}{_ext_for(mime, filename)}"
    path = settings.media_dir / name
    path.write_bytes(data)
    return str(path.relative_to(settings.data_dir))


def abs_path(rel: str) -> Path:
    p = (settings.data_dir / rel).resolve()
    if settings.data_dir.resolve() not in p.parents:
        raise ValueError("media path escapes data dir")
    return p


def delete_files(rel_paths: list[str]) -> None:
    for rel in rel_paths:
        try:
            abs_path(rel).unlink(missing_ok=True)
        except ValueError:
            continue
