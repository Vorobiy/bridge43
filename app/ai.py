"""Shared Claude client."""
from __future__ import annotations

from functools import lru_cache

from .config import settings


class AIConfigError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=120)


def client():
    if not settings.anthropic_api_key:
        raise AIConfigError("ANTHROPIC_API_KEY is not set")
    return _client(settings.anthropic_api_key)


def text_of(message) -> str:
    return "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text").strip()
