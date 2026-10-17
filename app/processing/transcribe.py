"""Speech-to-text for voice notes, call recordings and video soundtracks.

Two providers:
  * "openai" - any OpenAI-compatible /audio/transcriptions endpoint. Default is Groq's hosted
    Whisper large-v3 (fast, has a free tier); set TRANSCRIBE_BASE_URL=https://api.openai.com/v1
    and TRANSCRIBE_MODEL=whisper-1 to use OpenAI instead.
  * "local"  - faster-whisper running on this machine (free, needs `pip install faster-whisper`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import settings
from . import ffmpeg

# Context helps Whisper with Cameroonian names and Pidgin/French code-switching.
VOCAB_HINT = (
    "Conversation between PS43 Foundation and a school in Cameroon, in English, French or Cameroonian "
    "Pidgin English. Words: PS43, Pascal Siakam, Bamenda, Buea, Douala, Yaoundé, Bafoussam, Garoua, Maroua, "
    "Kumba, Limbe, Ngaoundéré, Bertoua, Ebolowa, GBHS, GBSS, GSS, GTC, lycée, CES, école publique, "
    "headmaster, principal, proviseur, directeur, PTA, APEE, latrine, salle de classe, tôle, banc, pupitre."
)

LANG_NAMES = {"en": "english", "fr": "french", "english": "english", "french": "french"}


class TranscriptionError(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: str | None
    duration: float | None
    model: str
    segments: list[dict] | None = None


def transcribe(path: Path) -> Transcript:
    if not ffmpeg.available():
        raise TranscriptionError("ffmpeg is not installed")
    if not ffmpeg.has_audio_stream(path):
        return Transcript(text="", language=None, duration=ffmpeg.duration_seconds(path), model="none")
    with ffmpeg.workdir() as tmp:
        speech = ffmpeg.to_speech_mp3(path, Path(tmp))
        duration = ffmpeg.duration_seconds(speech)
        if settings.transcribe_provider == "local":
            return _local(speech, duration)
        return _openai_compatible(speech, duration)


def _openai_compatible(speech: Path, duration: float | None) -> Transcript:
    if not settings.transcribe_api_key:
        raise TranscriptionError("TRANSCRIBE_API_KEY is not set (or set TRANSCRIBE_PROVIDER=local)")
    url = f"{settings.transcribe_base_url}/audio/transcriptions"
    data = {"model": settings.transcribe_model, "prompt": VOCAB_HINT}
    # verbose_json returns the detected language and timestamps (Whisper models);
    # newer gpt-4o-transcribe models only accept json.
    data["response_format"] = "json" if "gpt-4o" in settings.transcribe_model else "verbose_json"
    with speech.open("rb") as fh:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {settings.transcribe_api_key}"},
            data=data,
            files={"file": (speech.name, fh, "audio/mpeg")},
            timeout=300,
        )
    if resp.status_code >= 400:
        raise TranscriptionError(f"transcription API {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    segments = [
        {"start": round(s.get("start", 0), 1), "end": round(s.get("end", 0), 1), "text": s.get("text", "").strip()}
        for s in body.get("segments") or []
    ]
    return Transcript(
        text=(body.get("text") or "").strip(),
        language=body.get("language"),
        duration=body.get("duration") or duration,
        model=settings.transcribe_model,
        segments=segments or None,
    )


def _local(speech: Path, duration: float | None) -> Transcript:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise TranscriptionError("TRANSCRIBE_PROVIDER=local needs `pip install faster-whisper`") from exc
    model = _local_model(settings.local_whisper_model)
    segs, info = model.transcribe(str(speech), initial_prompt=VOCAB_HINT, vad_filter=True)
    segments = [{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()} for s in segs]
    return Transcript(
        text=" ".join(s["text"] for s in segments).strip(),
        language=info.language,
        duration=info.duration or duration,
        model=f"faster-whisper-{settings.local_whisper_model}",
        segments=segments,
    )


_LOCAL_CACHE: dict = {}


def _local_model(name: str):  # pragma: no cover - optional dependency
    if name not in _LOCAL_CACHE:
        from faster_whisper import WhisperModel

        _LOCAL_CACHE[name] = WhisperModel(name, device="auto", compute_type="int8")
    return _LOCAL_CACHE[name]


def format_with_timestamps(t: Transcript) -> str:
    """Transcript text with [mm:ss] markers so the case note can cite a moment in a call."""
    if not t.segments:
        return t.text
    lines = []
    for s in t.segments:
        m, sec = divmod(int(s["start"]), 60)
        lines.append(f"[{m:02d}:{sec:02d}] {s['text']}")
    return "\n".join(lines)
