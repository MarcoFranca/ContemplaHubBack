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
# Gênero pelo primeiro nome (heurística PT-BR) para escolher a voz invertida.
# --------------------------------------------------------------------------- #
# Nomes terminados em "a" que são masculinos (a heurística padrão erraria).
_MASC_EXCECOES = {
    "joshua", "isaias", "josias", "elias", "jonas", "lucas", "tobias", "matias",
    "cauã", "caua", "juca", "noa", "aba", "aba",
}
# Nomes NÃO terminados em "a" que são femininos.
_FEM_EXCECOES = {
    "beatriz", "ines", "inês", "isis", "íris", "iris", "mercedes", "lais", "laís",
    "raquel", "isabel", "cristiane", "rute", "ruth", "ester", "esther", "carmen",
    "miriam", "jasmin", "yasmin", "nicole", "gabrielle", "michele", "michelle",
    "jaqueline", "jacqueline", "eloa", "eloá",
}


def _primeiro_nome(nome: Optional[str]) -> str:
    return (nome or "").strip().split()[0].lower() if (nome or "").strip() else ""


def _voz_por_genero(nome: Optional[str]) -> str:
    """Voz invertida: homem -> feminina; mulher -> masculina; indefinido -> feminina."""
    fem_voice = settings.OPENAI_TTS_VOICE_FEMININA.strip() or settings.OPENAI_TTS_VOICE
    masc_voice = settings.OPENAI_TTS_VOICE_MASCULINA.strip() or settings.OPENAI_TTS_VOICE
    n = _primeiro_nome(nome)
    if not n:
        return fem_voice  # sem nome: default feminino
    if n in _FEM_EXCECOES:
        return masc_voice  # cliente mulher -> voz masculina
    if n in _MASC_EXCECOES:
        return fem_voice  # cliente homem -> voz feminina
    # Heurística: termina em "a" (ou "ia") => feminino; caso contrário masculino.
    if n.endswith("a"):
        return masc_voice  # provavelmente mulher -> voz masculina
    return fem_voice  # provavelmente homem (ou indefinido) -> voz feminina


# --------------------------------------------------------------------------- #
# TTS — gera voz da resposta. Retorna (bytes, mime) ou None.
# --------------------------------------------------------------------------- #
def sintetizar(texto: str, nome_cliente: Optional[str] = None) -> Optional[tuple[bytes, str]]:
    texto = (texto or "").strip()
    if not texto:
        return None
    provider = settings.AUDIO_TTS_PROVIDER.lower()
    if provider == "elevenlabs":
        return _tts_elevenlabs(texto)
    return _tts_openai(texto, voice=_voz_por_genero(nome_cliente))


def _tts_openai(texto: str, voice: Optional[str] = None) -> Optional[tuple[bytes, str]]:
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
                "voice": (voice or settings.OPENAI_TTS_VOICE),
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
