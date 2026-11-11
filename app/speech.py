"""Speech helpers: Whisper-compatible STT and OpenAI-compatible TTS."""

from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger("speech")


class Speech:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    @property
    def stt_enabled(self) -> bool:
        return bool(self.settings.stt_url)

    @property
    def tts_enabled(self) -> bool:
        return bool(self.settings.tts_url)

    async def transcribe(self, path: str, filename: str) -> str:
        if not self.stt_enabled:
            raise RuntimeError("STT is not configured")
        url = self.settings.stt_url.rstrip("/") + "/audio/transcriptions"
        with open(path, "rb") as handle:
            files = {"file": (filename, handle, "application/octet-stream")}
            data = {
                "model": self.settings.stt_model,
                "language": self.settings.stt_language,
                "response_format": "json",
            }
            response = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.stt_key}"},
                data=data,
                files=files,
            )
        response.raise_for_status()
        payload = response.json()
        return (payload.get("text") or "").strip()

    async def synthesize(self, text: str, voice: str | None = None) -> tuple[bytes, str]:
        if not self.tts_enabled:
            raise RuntimeError("TTS is not configured")
        url = self.settings.tts_url.rstrip("/") + "/audio/speech"
        body = {
            "model": self.settings.tts_model,
            "input": text,
            "voice": voice or self.settings.tts_voice,
            "response_format": "opus",
        }
        response = await self._client.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.tts_key}"},
            json=body,
        )
        response.raise_for_status()
        audio = response.content
        kind = "ogg" if audio[:4] == b"OggS" else ("wav" if audio[:4] == b"RIFF" else "mp3")
        return audio, kind

    async def close(self) -> None:
        await self._client.aclose()
