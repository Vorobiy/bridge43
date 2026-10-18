"""Test setup: temp storage, synchronous background work, and fake AI/transcription providers."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, summary, triage
from app.config import settings
from app.processing import pipeline, transcribe, vision


class Fakes:
    def __init__(self):
        self.model_calls: list[str] = []
        self.model_output: dict | None = None
        self.triage_calls: list[str] = []


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "auto_summarize", False)
    monkeypatch.setattr(settings, "staff_password", "")
    monkeypatch.setattr(settings, "recorder_token", "tok")
    monkeypatch.setattr(settings, "public_base_url", "https://bridge.test")
    db.init_db()

    f = Fakes()
    # run background work inline so tests are deterministic
    monkeypatch.setattr(pipeline, "enqueue", lambda item_id: pipeline.process_item(item_id))
    monkeypatch.setattr(pipeline, "submit", lambda fn, *args: fn(*args))

    def fake_transcribe(path):
        return transcribe.Transcript(text=f"transcript of {path.name}: le toit fuit, 40 bancs", language="fr",
                                     duration=12.0, model="fake-whisper",
                                     segments=[{"start": 0.0, "end": 5.0, "text": "le toit fuit"},
                                               {"start": 65.0, "end": 70.0, "text": "40 bancs"}])

    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(vision, "describe_images",
                        lambda paths, what="photo", caption=None: f"{len(paths)} image(s): roof with holes")
    monkeypatch.setattr(vision, "describe_pdf", lambda path: "enrollment list: 412 students")

    def fake_model(prompt: str) -> dict:
        f.model_calls.append(prompt)
        if f.model_output is not None:
            return f.model_output
        return {
            "case_type": "need", "title": "GBHS Test - leaking roof",
            "organization": {"name": "GBHS Test", "type": "school", "region": "Northwest", "town": "Bali", "citations": [1, 999]},
            "situation": {"text": "Roof leaks in two classrooms.", "citations": [1]},
            "needs": [{"category": "roofing", "description": "Replace roof sheets", "quantity": "2 classrooms",
                       "urgency": "high", "citations": [1, 2, 12345]}],
            "offers": [], "assets": [],
            "readiness": {"electricity": {"status": "no", "note": None, "citations": [1]}},
            "numbers": {"students": 412, "teachers": None, "classrooms": 6, "citations": [1]},
            "people": [], "plan": [], "open_questions": ["Exact roof size?"], "safety_flags": [],
            "language": "French", "confidence": "medium",
            "confirmation_message": {"language": "French", "text": "PS43: nous avons compris..."},
        }

    monkeypatch.setattr(summary, "call_model", fake_model)

    def fake_triage(conversation: str):
        f.triage_calls.append(conversation)
        low = conversation.lower()
        personal = ("dinner", "basketball", "lol", "mom", "birthday")
        return not any(w in low for w in personal), "fake"

    monkeypatch.setattr(triage, "is_relevant", fake_triage)
    return f


@pytest.fixture
def client(fakes):
    from app.main import app

    with TestClient(app) as c:
        yield c


_ids = iter(range(1, 10**9))


def send_whatsapp(client, phone, text=None, *, name=None, files=None, kind=None, from_me=False, msg_id=None,
                  chat_id=None, at=None, avatar=None):
    """A WhatsApp message arriving through the PS43 laptop (the menu bar app reads WhatsApp Desktop).

    files: [(filename, bytes)] attached to this one message (photo, voice note...).
    """
    import json
    msgs, uploads = [], []
    mid = msg_id if msg_id is not None else next(_ids)
    if files:
        for i, (fname, data) in enumerate(files):
            ext = fname.rsplit(".", 1)[-1].lower()
            k = kind or ("audio" if ext in ("opus", "ogg", "m4a", "mp3") else "video" if ext in ("mp4", "mov") else
                         "image" if ext in ("jpg", "jpeg", "png", "webp") else "document")
            msgs.append({"id": f"{mid}-{i}", "at": at, "from_me": from_me, "kind": k, "text": text if i == 0 else "",
                         "file": fname})
            uploads.append(("files", (fname, data, "application/octet-stream")))
    else:
        msgs.append({"id": mid, "at": at, "from_me": from_me, "kind": "text", "text": text or ""})
    chat = {"chat_id": chat_id or f"{phone.lstrip('+').replace(' ', '')}@s.whatsapp.net", "phone": phone, "name": name or "",
            "messages": msgs}
    if avatar:
        uploads.append(("avatar", ("avatar.jpg", avatar, "image/jpeg")))
    return client.post("/api/recorder/messages", data={"chat": json.dumps(chat)}, files=uploads,
                       headers={"Authorization": "Bearer tok"})


@pytest.fixture
def wa(client):
    return lambda *a, **k: send_whatsapp(client, *a, **k)
