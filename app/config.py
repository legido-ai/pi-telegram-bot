"""Environment-backed settings for the Pi Telegram bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "")
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


@dataclass
class Settings:
    token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    trigger: str = "mention"

    data_dir: str = "/data"
    workspace_dir: str = "/workspace"
    session_dir: str = "/data/sessions"

    pi_bin: str = "pi"
    pi_provider: str = "opencode-go"
    pi_model: str = "deepseek-v4.1-flash"
    pi_thinking: str = ""

    stt_url: str = ""
    stt_key: str = "local"
    stt_model: str = "small"
    stt_language: str = "es"

    tts_url: str = ""
    tts_key: str = "local"
    tts_model: str = "piper"
    tts_voice: str = "es-ES-ElviraNeural"
    tts_mode: str = "off"  # off | auto | all

    health_host: str = "127.0.0.1"
    health_port: int = 8080
    edit_interval: float = 2.0
    prompt_timeout: int = 3600

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required")
        data_dir = os.getenv("DATA_DIR", "/data")
        return cls(
            token=token,
            allowed_user_ids=_ids("TELEGRAM_ALLOWED_USER_IDS") or _ids("TELEGRAM_ALLOWED_USER_ID"),
            trigger=os.getenv("TELEGRAM_TRIGGER", "mention"),
            data_dir=data_dir,
            workspace_dir=os.getenv("WORKSPACE_DIR", "/workspace"),
            session_dir=os.getenv("PI_SESSION_DIR", f"{data_dir}/sessions"),
            pi_bin=os.getenv("PI_BIN", "pi"),
            pi_provider=os.getenv("PI_PROVIDER", "opencode-go"),
            pi_model=os.getenv("PI_MODEL", "deepseek-v4.1-flash"),
            pi_thinking=os.getenv("PI_THINKING", ""),
            stt_url=os.getenv("STT_API_URL", ""),
            stt_key=os.getenv("STT_API_KEY", "local"),
            stt_model=os.getenv("STT_MODEL", "small"),
            stt_language=os.getenv("STT_LANGUAGE", "es"),
            tts_url=os.getenv("TTS_API_URL", ""),
            tts_key=os.getenv("TTS_API_KEY", "local"),
            tts_model=os.getenv("TTS_MODEL", "piper"),
            tts_voice=os.getenv("TTS_VOICE", "es-ES-ElviraNeural"),
            tts_mode=os.getenv("TTS_MODE", "off"),
            health_host=os.getenv("HEALTH_HOST", "127.0.0.1"),
            health_port=_int("HEALTH_PORT", 8080),
            edit_interval=float(os.getenv("EDIT_INTERVAL", "2.0")),
            prompt_timeout=_int("PROMPT_TIMEOUT", 3600),
        )
