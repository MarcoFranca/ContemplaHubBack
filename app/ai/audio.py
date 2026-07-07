"""Áudio do agente: transcrição (STT) e voz (TTS), com provedor plugável.

Hoje: OpenAI (Whisper transcreve, TTS fala) com uma única chave. A camada é
abstrata para trocar a voz por ElevenLabs no futuro sem mexer no resto.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_OPENAI_BASE = "https://api.openai.com/v1"


# --------------------------------------------------------------------------- #
# STT — transcrição do áudio recebido
# --------------------------------------------------------------------------- #
def transcrever(audio_bytes: bytes, mime: str = "audio/ogg") -> Optional[str]:
    """Transcreve áudio para texto (pt-BR). Retorna None em falha."""
    key = settings.OPENAI_API_KEY.strip()
    if not key or not audio_bytes:
        return None
    ext = "ogg"
    if "mp3" in mime or "mpeg" in mime:
        ext = "mp3"
    elif "wav" in mime:
        ext = "wav"
    elif "m4a" in mime or "mp4" in mime:
        ext = "m4a"
    try:
        resp = requests.post(
            f"{_OPENAI_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (f"audio.{ext}", audio_bytes, mime)},
            data={"model": settings.OPENAI_STT_MODEL, "language": "pt"},
            timeout=60,
        )
        if resp.status_code >= 400:
            logger.warning("stt_falhou", extra={"status": resp.status_code, "body": resp.text[:300]})
            return None
        data = resp.json()
        return (data.get("text") or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("stt_erro", extra={"error": str(exc)})
        return None


# --------------------------------------------------------------------------- #
# TTS — gera voz da resposta. Retorna (bytes, mime) ou None.
# --------------------------------------------------------------------------- #
def sintetizar(texto: str) -> Optional[tuple[bytes, str]]:
    texto = (texto or "").strip()
    if not texto:
        return None
    provider = settings.AUDIO_TTS_PROVIDER.lower()
    if provider == "elevenlabs":
        return _tts_elevenlabs(texto)
    return _tts_openai(texto)


def _tts_openai(texto: str) -> Optional[tuple[bytes, str]]:
    key = settings.OPENAI_API_KEY.strip()
    if not key:
        return None
    try:
        resp = requests.post(
            f"{_OPENAI_BASE}/audio/speech",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": settings.OPENAI_TTS_MODEL,
                "input": texto,
                "voice": settings.OPENAI_TTS_VOICE,
                "response_format": "opus",  # ogg/opus = nota de voz no WhatsApp
            },
            timeout=60,
        )
        if resp.status_code >= 400:
            logger.warning("tts_openai_falhou", extra={"status": resp.status_code, "body": resp.text[:300]})
            return None
        return resp.content, "audio/ogg"
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts_openai_erro", extra={"error": str(exc)})
        return None


def _tts_elevenlabs(texto: str) -> Optional[tuple[bytes, str]]:
    key = settings.ELEVENLABS_API_KEY.strip()
    voice = settings.ELEVENLABS_VOICE_ID.strip()
    if not key or not voice:
        logger.warning("tts_elevenlabs_sem_config")
        return None
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/ogg"},
            json={"text": texto, "model_id": "eleven_multilingual_v2", "output_format": "opus_48000_128"},
            timeout=60,
        )
        if resp.status_code >= 400:
            logger.warning("tts_elevenlabs_falhou", extra={"status": resp.status_code, "body": resp.text[:300]})
            return None
        return resp.content, "audio/ogg"
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts_elevenlabs_erro", extra={"error": str(exc)})
        return None
