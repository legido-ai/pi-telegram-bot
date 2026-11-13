"""Telegram front end for a Pi RPC session, one conversation per chat/topic."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time

from telegram import Message, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .pi import PiError, PiSession
from .speech import Speech

log = logging.getLogger("bot")

MAX_MESSAGE = 3800
EDIT_INTERVAL = 2.0


def conversation_key(update: Update) -> str:
    chat = update.effective_chat
    thread = update.effective_message.message_thread_id if update.effective_message else None
    return f"{chat.id}:{thread or 0}"


def format_elapsed(seconds: float) -> str:
    """Elapsed time for the status bubble: 42s, then 4m 58s, then 1h 02m 03s."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def split_text(text: str, size: int = MAX_MESSAGE) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    while text:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


class SettingsStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self.data: dict = {}
        try:
            with open(path, encoding="utf-8") as handle:
                self.data = json.load(handle)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str, default):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle)


class Bridge:
    def __init__(self, settings: Settings, speech: Speech) -> None:
        self.settings = settings
        self.speech = speech
        self.store = SettingsStore(os.path.join(settings.data_dir, "settings.json"))
        self.sessions: dict[str, PiSession] = {}
        self.queues: dict[str, asyncio.Queue] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.busy: dict[str, bool] = {}

    # ------------------------------------------------------------------ plumbing
    def session(self, key: str) -> PiSession:
        if key not in self.sessions:
            self.sessions[key] = PiSession(self.settings, key)
        return self.sessions[key]

    def enqueue(self, key: str, job) -> None:
        queue = self.queues.setdefault(key, asyncio.Queue())
        queue.put_nowait(job)
        worker = self.workers.get(key)
        if worker is None or worker.done():
            self.workers[key] = asyncio.create_task(self._worker(key, queue))

    async def _worker(self, key: str, queue: asyncio.Queue) -> None:
        while True:
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await job()
            except Exception:  # noqa: BLE001
                log.exception("job failed for %s", key)
            finally:
                queue.task_done()

    # ------------------------------------------------------------------ trigger
    async def addressed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return False
        if chat.type == ChatType.PRIVATE:
            return True
        if self.settings.trigger != "mention":
            return True
        if message.reply_to_message and message.reply_to_message.from_user:
            if message.reply_to_message.from_user.id == context.bot.id:
                return True
        text = message.text or message.caption or ""
        username = context.bot.username or ""
        if f"@{username}".lower() in text.lower():
            return True
        return False

    @staticmethod
    def clean(text: str, username: str) -> str:
        if username:
            text = text.replace(f"@{username}", "")
        return text.strip()

    # ------------------------------------------------------------------ handlers
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if self.settings.allowed_user_ids and user.id not in self.settings.allowed_user_ids:
            return
        if not await self.addressed(update, context):
            return
        text = self.clean(message.text or message.caption or "", context.bot.username or "")
        if not text:
            return
        prompt = f"[speaker] {user.full_name} | telegram_id: {user.id}\n\n{text}"
        self.enqueue(conversation_key(update), lambda: self.run_prompt(update, context, prompt, voice=False))

    async def on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if self.settings.allowed_user_ids and user.id not in self.settings.allowed_user_ids:
            return
        media = message.voice or message.audio or message.video_note
        if media is None:
            return
        if not await self.addressed(update, context):
            return
        if not self.speech.stt_enabled:
            await message.reply_text("Voice input is not configured (STT_API_URL is empty).")
            return

        async def job() -> None:
            note = await message.reply_text("Transcribing…")
            path = ""
            try:
                handle = await context.bot.get_file(media.file_id)
                path = os.path.join(self.settings.data_dir, f"voice-{media.file_unique_id}.oga")
                await handle.download_to_drive(path)
                transcript = await self.speech.transcribe(path, os.path.basename(path))
            except Exception as exc:  # noqa: BLE001
                log.warning("transcription failed: %s", exc)
                await note.edit_text("I could not transcribe that audio.")
                return
            finally:
                if path:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if not transcript:
                await note.edit_text("The audio was empty after transcription.")
                return
            await note.edit_text(f"🎙️ {transcript[:3500]}")
            prompt = (
                f"[speaker] {user.full_name} | telegram_id: {user.id}\n"
                f"[voice note, transcribed]\n\n{transcript}"
            )
            await self.run_prompt(update, context, prompt, voice=True)

        self.enqueue(conversation_key(update), job)

    async def run_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, voice: bool) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        key = conversation_key(update)
        session = self.session(key)
        placeholder = await message.reply_text("…")
        buffer: list[str] = []
        last_edit = 0.0
        started = time.monotonic()
        stop = asyncio.Event()

        async def keep_typing() -> None:
            # Telegram clears the typing indicator after ~5 s. Refresh it for
            # the whole turn so a slow tool call never looks like a dead bot.
            while not stop.is_set():
                try:
                    await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(4)

        async def pump() -> None:
            nonlocal last_edit
            while not stop.is_set():
                await asyncio.sleep(0.4)
                text = "".join(buffer)
                now = time.monotonic()
                if text and now - last_edit >= EDIT_INTERVAL:
                    last_edit = now
                    try:
                        await placeholder.edit_text(text[:MAX_MESSAGE])
                    except BadRequest:
                        pass
                elif not text and now - started >= 8 and now - last_edit >= 5:
                    # No streamed text yet: show elapsed time instead of "…".
                    last_edit = now
                    try:
                        await placeholder.edit_text(f"⏳ Working… {format_elapsed(now - started)}")
                    except BadRequest:
                        pass

        typing_task = asyncio.create_task(keep_typing())
        pump_task = asyncio.create_task(pump())
        log.info("prompt started chat=%s", chat.id)
        try:
            answer = await session.prompt(prompt, on_delta=buffer.append)
        except PiError as exc:
            stop.set()
            pump_task.cancel()
            typing_task.cancel()
            await placeholder.edit_text(f"Pi error: {exc}"[:MAX_MESSAGE])
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("prompt failed")
            stop.set()
            pump_task.cancel()
            typing_task.cancel()
            await placeholder.edit_text(f"Pi failed: {exc}"[:MAX_MESSAGE])
            return
        stop.set()
        pump_task.cancel()
        typing_task.cancel()
        log.info("prompt done chat=%s chars=%d", chat.id, len(answer))
        answer = answer or "(empty answer)"
        chunks = split_text(answer)
        try:
            await placeholder.edit_text(chunks[0], parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            try:
                await placeholder.edit_text(chunks[0])
            except BadRequest:
                pass
        for chunk in chunks[1:]:
            try:
                await message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
            except BadRequest:
                await message.reply_text(chunk)
        mode = str(self.store.get("ttsMode", self.settings.tts_mode))
        if self.speech.tts_enabled and (mode == "all" or (mode == "auto" and voice)):
            await self.speak(chat, answer)

    async def speak(self, chat, text: str) -> None:
        try:
            audio, kind = await self.speech.synthesize(text[:1500])
        except Exception as exc:  # noqa: BLE001
            log.warning("tts failed: %s", exc)
            return
        stream = io.BytesIO(audio)
        stream.name = f"answer.{kind}"
        await chat.send_action(ChatAction.RECORD_VOICE)
        if kind == "ogg":
            await chat.send_voice(voice=stream)
        else:
            await chat.send_audio(audio=stream, title="Pi")

    # ------------------------------------------------------------------ commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            "Pi is ready. Send a message, or a voice note if STT is configured.\n"
            "/new — fresh session\n/stop — abort the current turn\n"
            "/status — session numbers\n/tts off|auto|all — spoken replies"
        )

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        key = conversation_key(update)
        await self.session(key).new_session()
        await update.effective_message.reply_text("New Pi session.")

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        key = conversation_key(update)
        await self.session(key).abort()
        await update.effective_message.reply_text("Stopped.")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        key = conversation_key(update)
        state = await self.session(key).state()
        model = (state.get("model") or {}).get("id") or self.settings.pi_model
        lines = [
            f"model: {model}",
            f"session: {state.get('sessionId', '?')}",
            f"messages: {state.get('messageCount', '?')}",
            f"streaming: {state.get('isStreaming')}",
            f"tts: {self.store.get('ttsMode', self.settings.tts_mode)}",
        ]
        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_tts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        value = (args[0] if args else "").lower()
        if value not in {"off", "auto", "all"}:
            current = self.store.get("ttsMode", self.settings.tts_mode)
            await update.effective_message.reply_text(f"tts: {current}. Use /tts off|auto|all")
            return
        self.store.set("ttsMode", value)
        await update.effective_message.reply_text(f"tts: {value}")

    async def cmd_say(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Explicit audio: /say [voice] text — nothing but the voice note."""
        message = update.effective_message
        text = " ".join(context.args or []).strip()
        if not text:
            await message.reply_text("Use /say <text>  ·  /say en <text>  ·  /say <voice-name> <text>")
            return
        if not self.speech.tts_enabled:
            await message.reply_text("TTS is not configured (TTS_API_URL is empty).")
            return
        voice = self.settings.tts_voice
        first, _, rest = text.partition(" ")
        if rest and first.lower() in {"en", "es", "english", "spanish"}:
            voice = "en-US-AriaNeural" if first.lower() in {"en", "english"} else "es-ES-ElviraNeural"
            text = rest
        elif rest and ("-" in first and first.count("-") >= 2):
            voice = first
            text = rest
        try:
            audio, kind = await self.speech.synthesize(text[:1500], voice=voice)
        except Exception as exc:  # noqa: BLE001
            log.warning("tts failed: %s", exc)
            await message.reply_text("I could not synthesize that audio.")
            return
        stream = io.BytesIO(audio)
        stream.name = f"say.{kind}"
        if kind == "ogg":
            await message.reply_voice(voice=stream)
        else:
            await message.reply_audio(audio=stream, title="Pi")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.cmd_start(update, context)
