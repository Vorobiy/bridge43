"""Parse WhatsApp's built-in "Export chat" (.txt, or .zip with media) from iPhone or Android.

This is the zero-integration path: staff export a chat with a school from their phone and
upload it. Works today, costs nothing, needs no WhatsApp API.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath

INVISIBLE = "‎‏‪‬﻿"

# iOS:     [17/10/2026, 09:30:12] Name: text
# Android: 17/10/2026, 09:30 - Name: text        (also "17/10/26 09:30 - ", "10/17/26, 9:30 AM - ")
HEADER = re.compile(
    r"^\[?(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[APap]\.?\s?[Mm]\.?)?)\]?"
    r"(?:\s+-\s+|\s+)"
    r"(?P<rest>.*)$"
)
ATTACHED_IOS = re.compile(r"<(?:attached|pièce jointe|joint)\s*:\s*(?P<file>[^>]+)>", re.I)
ATTACHED_ANDROID = re.compile(r"^(?P<file>[\w\-. ]+\.\w{2,5})\s+\((?:file attached|fichier joint|archivo adjunto)\)", re.I)
OMITTED = re.compile(r"<(?:media omitted|médias omis|media omis|image omitted|audio omitted|video omitted|"
                     r"image absente|audio omis|vidéo absente)>|^(?:image|audio|video|vidéo) omitted$", re.I)


@dataclass
class ExportMessage:
    sent_at: datetime
    author: str
    text: str
    attachment: str | None = None      # file name inside the export
    media_omitted: bool = False


@dataclass
class ParsedExport:
    messages: list[ExportMessage]
    files: dict[str, bytes] = field(default_factory=dict)  # attachment name -> bytes (zip exports)
    authors: list[str] = field(default_factory=list)


def _clean(s: str) -> str:
    return s.translate({ord(c): None for c in INVISIBLE}).strip()


def _parse_dt(date: str, time: str, day_first: bool) -> datetime | None:
    parts = re.split(r"[./-]", date)
    if len(parts) != 3:
        return None
    a, b, c = (int(p) for p in parts)
    if a > 31:                       # yyyy-mm-dd
        y, m, d = a, b, c
    else:
        if a > 12:
            day_first = True
        elif b > 12:
            day_first = False
        d, m = (a, b) if day_first else (b, a)
        y = c + 2000 if c < 100 else c
    t = time.replace(".", ":").strip()
    pm = re.search(r"p\.?\s?m", t, re.I)
    am = re.search(r"a\.?\s?m", t, re.I)
    t = re.sub(r"\s?[APap]\.?\s?[Mm]\.?", "", t)
    hms = [int(x) for x in t.split(":")]
    h, mi, s = hms[0], hms[1], hms[2] if len(hms) > 2 else 0
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    try:
        return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)
    except ValueError:
        return None


def _guess_day_first(lines: list[str]) -> bool:
    for ln in lines:
        m = HEADER.match(_clean(ln))
        if m:
            parts = re.split(r"[./-]", m.group("date"))
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                if int(parts[0]) > 12:
                    return True
                if int(parts[1]) > 12:
                    return False
    return True  # Cameroon and most of the world write day first


def parse_text(text: str, day_first: bool | None = None) -> list[ExportMessage]:
    lines = text.splitlines()
    if day_first is None:
        day_first = _guess_day_first(lines)
    messages: list[ExportMessage] = []
    for raw in lines:
        line = _clean(raw)
        m = HEADER.match(line)
        dt = _parse_dt(m.group("date"), m.group("time"), day_first) if m else None
        if m and dt:
            rest = m.group("rest")
            if ": " not in rest:
                continue  # system line, e.g. "Messages are end-to-end encrypted"
            author, body = rest.split(": ", 1)
            author, body = _clean(author), _clean(body)
            msg = ExportMessage(sent_at=dt, author=author, text=body)
            att = ATTACHED_IOS.search(body) or ATTACHED_ANDROID.match(body)
            if att:
                msg.attachment = _clean(att.group("file"))
                msg.text = _clean(body.replace(att.group(0), ""))
            elif OMITTED.search(body):
                msg.media_omitted = True
                msg.text = ""
            messages.append(msg)
        elif messages and line:
            messages[-1].text = (messages[-1].text + "\n" + line).strip()
    return messages


def parse_upload(filename: str, data: bytes, day_first: bool | None = None) -> ParsedExport:
    """Accept a .txt export or a .zip export (chat text + media)."""
    files: dict[str, bytes] = {}
    if filename.lower().endswith(".zip") or data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            txt_names = [n for n in zf.namelist() if n.lower().endswith(".txt") and not n.startswith("__MACOSX")]
            if not txt_names:
                raise ValueError("no chat .txt file inside the zip")
            chat_name = next((n for n in txt_names if "chat" in PurePosixPath(n).name.lower()), txt_names[0])
            text = zf.read(chat_name).decode("utf-8-sig", errors="replace")
            for info in zf.infolist():
                if info.is_dir() or info.filename == chat_name or info.filename.startswith("__MACOSX"):
                    continue
                if info.file_size > 64 * 1024 * 1024:
                    continue  # skip very large files
                files[PurePosixPath(info.filename).name] = zf.read(info)
    else:
        text = data.decode("utf-8-sig", errors="replace")
    messages = parse_text(text, day_first)
    authors = list(dict.fromkeys(m.author for m in messages))
    return ParsedExport(messages=messages, files=files, authors=authors)


def message_key(contact_address: str, msg: ExportMessage) -> str:
    """Stable id so re-uploading the same export does not duplicate messages."""
    h = hashlib.sha1(f"{contact_address}|{msg.sent_at.isoformat()}|{msg.author}|{msg.text}|{msg.attachment}".encode())
    return "export:" + h.hexdigest()[:20]
