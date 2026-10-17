"""Runtime configuration, read from environment variables (or a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: load a local .env when python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass
class Settings:
    # Storage
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))

    # Public URL of this server (used to build Twilio callback URLs and verify signatures)
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))

    # Staff login for the review screen (HTTP Basic)
    staff_username: str = field(default_factory=lambda: os.getenv("STAFF_USERNAME", "ps43"))
    staff_password: str = field(default_factory=lambda: os.getenv("STAFF_PASSWORD", ""))

    # Claude
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_workspace_id: str = field(default_factory=lambda: os.getenv("ANTHROPIC_WORKSPACE_ID", ""))
    summary_model: str = field(default_factory=lambda: os.getenv("SUMMARY_MODEL", "claude-haiku-5-5"))
    vision_model: str = field(default_factory=lambda: os.getenv("VISION_MODEL", "claude-haiku-5-5"))
    staff_language: str = field(default_factory=lambda: os.getenv("STAFF_LANGUAGE", "English"))
    auto_summarize: bool = field(default_factory=lambda: _bool("AUTO_SUMMARIZE", True))

    # Transcription: "openai" (any OpenAI-compatible /audio/transcriptions API: OpenAI, Groq...)
    # or "local" (faster-whisper on this machine, free).
    transcribe_provider: str = field(default_factory=lambda: os.getenv("TRANSCRIBE_PROVIDER", "openai"))
    transcribe_base_url: str = field(default_factory=lambda: os.getenv("TRANSCRIBE_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"))
    transcribe_api_key: str = field(default_factory=lambda: os.getenv("TRANSCRIBE_API_KEY", ""))
    transcribe_model: str = field(default_factory=lambda: os.getenv("TRANSCRIBE_MODEL", "whisper-large-v3"))
    local_whisper_model: str = field(default_factory=lambda: os.getenv("LOCAL_WHISPER_MODEL", "small"))

    # Twilio (WhatsApp, SMS, voice and WhatsApp calls)
    twilio_account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    twilio_whatsapp_from: str = field(default_factory=lambda: os.getenv("TWILIO_WHATSAPP_FROM", ""))  # e.g. whatsapp:+14155238886
    twilio_sms_from: str = field(default_factory=lambda: os.getenv("TWILIO_SMS_FROM", ""))  # e.g. +1555...
    validate_twilio_signature: bool = field(default_factory=lambda: _bool("VALIDATE_TWILIO_SIGNATURE", True))
    # If set, inbound calls are forwarded to this staff phone and the conversation is recorded.
    # If empty, callers leave a recorded message instead.
    staff_forward_number: str = field(default_factory=lambda: os.getenv("STAFF_FORWARD_NUMBER", ""))
    max_voicemail_seconds: int = field(default_factory=lambda: _int("MAX_VOICEMAIL_SECONDS", 300))

    # KoBoToolbox (offline field forms)
    kobo_webhook_secret: str = field(default_factory=lambda: os.getenv("KOBO_WEBHOOK_SECRET", ""))
    kobo_api_token: str = field(default_factory=lambda: os.getenv("KOBO_API_TOKEN", ""))
    kobo_contact_fields: list[str] = field(
        default_factory=lambda: [f.strip() for f in os.getenv("KOBO_CONTACT_FIELDS", "phone,school_phone,school_name").split(",") if f.strip()]
    )

    # Consent: if true, nothing a new contact sends is processed until they reply YES/OUI.
    require_explicit_consent: bool = field(default_factory=lambda: _bool("REQUIRE_EXPLICIT_CONSENT", False))

    # A contact's messages are grouped into one intake thread until it is approved/rejected
    # or goes quiet for this many hours.
    thread_idle_hours: int = field(default_factory=lambda: _int("THREAD_IDLE_HOURS", 72))

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bridge43.db"

    @property
    def twilio_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    def ensure_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
