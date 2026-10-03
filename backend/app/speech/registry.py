"""Which speech provider the app uses, and whether one is set up.

Tests register a stub with :func:`register_provider`; without one, the
ElevenLabs provider is built from ``ELEVENLABS_API_KEY``. A missing key is not
an error: the audio panel shows :data:`SETUP_MESSAGE` instead.
"""

from __future__ import annotations

from app.config import get_settings
from app.speech.base import SpeechProvider

SETUP_MESSAGE = (
    "ElevenLabs ist nicht eingerichtet: ELEVENLABS_API_KEY fehlt. Lokal in .env setzen, "
    "auf Cloud Run als Secret elevenlabs-api-key einbinden, dann neu starten."
)

_override: SpeechProvider | None = None


def register_provider(provider: SpeechProvider) -> None:
    global _override
    _override = provider


def reset_provider() -> None:
    global _override
    _override = None


def speech_configured() -> bool:
    return _override is not None or get_settings().has_speech_key()


def speech_provider() -> SpeechProvider | None:
    """The provider to use, or ``None`` when no key is set."""
    if _override is not None:
        return _override
    settings = get_settings()
    if not settings.has_speech_key():
        return None
    from app.speech.elevenlabs import ElevenLabsProvider

    return ElevenLabsProvider(settings.elevenlabs_api_key)
