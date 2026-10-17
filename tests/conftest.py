"""Test setup: temp storage, synchronous background work, and fake AI/transcription providers."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, summary
from app.config import settings
from app.processing import pipeline, transcribe, vision


class Fakes:
    def __init__(self):
        self.model_calls: list[str] = []
        self.model_output: dict | None = None


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "auto_summarize", False)
    monkeypatch.setattr(settings, "staff_password", "")
    monkeypatch.setattr(settings, "require_explicit_consent", False)
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "twilio_account_sid", "")
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
    return f


@pytest.fixture
def client(fakes):
    from app.main import app

    with TestClient(app) as c:
        yield c
