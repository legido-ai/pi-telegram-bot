"""Minimal client for Pi's RPC mode (JSONL over stdin/stdout).

One PiSession wraps one child process and therefore one conversation. The
process is started lazily and kept alive, so the session history lives in Pi
(and in its session file) instead of this bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable

from .config import Settings

log = logging.getLogger("pi")

_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def slug(value: str) -> str:
    return _SLUG.sub("-", value).strip("-")[:80] or "default"


class PiError(RuntimeError):
    pass


class PiSession:
    def __init__(self, settings: Settings, conversation: str) -> None:
        self.settings = settings
        self.conversation = conversation
        self.session_id = slug(conversation)
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._settled: asyncio.Event | None = None
        self._on_delta: Callable[[str], None] | None = None
        self._text: list[str] = []
        self._last_error: str = ""
        self._lock = asyncio.Lock()
        self._counter = 0

    # ------------------------------------------------------------------ process
    async def _spawn(self) -> None:
        os.makedirs(self.settings.session_dir, exist_ok=True)
        args = [
            self.settings.pi_bin,
            "--mode",
            "rpc",
            "--provider",
            self.settings.pi_provider,
            "--model",
            self.settings.pi_model,
            "--session-dir",
            self.settings.session_dir,
            "--session-id",
            self.session_id,
            "--name",
            f"telegram/{self.session_id}",
            "--append-system-prompt",
            "/app/telegram-prompt.md",
        ]
        if self.settings.pi_thinking:
            args += ["--thinking", self.settings.pi_thinking]
        log.info("starting pi session=%s", self.session_id)
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.settings.workspace_dir,
        )
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr = asyncio.create_task(self._read_stderr())
        self._reader.add_done_callback(self._reader_done)
        self._stderr.add_done_callback(self._stderr_done)

    def _reader_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("pi stdout reader stopped: %s", exc, exc_info=exc)

    def _stderr_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("pi stderr reader stopped: %s", exc, exc_info=exc)

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while line := await self._proc.stderr.readline():
            log.debug("pi stderr: %s", line.decode("utf-8", "replace").rstrip())

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        buffer = b""
        try:
            while True:
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        log.warning("pi sent a non-JSON line")
                        continue
                    try:
                        self._dispatch(record)
                    except Exception:  # noqa: BLE001
                        log.exception("pi event handling failed; ignoring record")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"pi output reader failed: {exc}"
            log.exception("pi stdout reader crashed")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(PiError(self._last_error or "pi exited"))
            self._pending.clear()
            if self._settled and not self._settled.is_set():
                self._settled.set()

    def _dispatch(self, record: object) -> None:
        if not isinstance(record, dict):
            log.warning("pi sent a non-object record (%s)", type(record).__name__)
            return
        kind = record.get("type")
        if kind == "response":
            future = self._pending.pop(str(record.get("id")), None)
            if future and not future.done():
                if record.get("success"):
                    data = record.get("data")
                    future.set_result(data if isinstance(data, dict) else {})
                else:
                    future.set_exception(PiError(record.get("error") or "command failed"))
            return
        if kind == "message_update":
            event = record.get("assistantMessageEvent")
            if isinstance(event, dict) and event.get("type") == "text_delta" and event.get("delta"):
                self._text.append(event["delta"])
                if self._on_delta:
                    self._on_delta(event["delta"])
            return
        if kind == "message_end":
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant" and message.get("errorMessage"):
                self._last_error = str(message["errorMessage"])
            return
        if kind == "agent_settled" and self._settled:
            self._settled.set()

    async def _command(self, payload: dict, timeout: float = 30) -> dict:
        if self._proc is None or self._proc.returncode is not None:
            await self._spawn()
        assert self._proc and self._proc.stdin
        self._counter += 1
        payload = {**payload, "id": str(self._counter)}
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[payload["id"]] = future
        self._proc.stdin.write((json.dumps(payload) + "\n").encode())
        await self._proc.stdin.drain()
        return await asyncio.wait_for(future, timeout=timeout)

    # ------------------------------------------------------------------ public
    async def prompt(self, text: str, on_delta: Callable[[str], None] | None = None) -> str:
        async with self._lock:
            self._text = []
            self._last_error = ""
            self._on_delta = on_delta
            self._settled = asyncio.Event()
            try:
                data = await self._command({"type": "prompt", "message": text})
            except PiError:
                self._on_delta = None
                raise
            if data.get("disposition") == "handled":
                self._on_delta = None
                return "".join(self._text)
            try:
                await asyncio.wait_for(self._settled.wait(), timeout=self.settings.prompt_timeout)
            except asyncio.TimeoutError as exc:
                await self._kill()
                raise PiError("Pi timed out") from exc
            self._on_delta = None
            answer = "".join(self._text).strip()
            if not answer and self._last_error:
                raise PiError(self._last_error)
            return answer

    async def abort(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            await self._command({"type": "abort"}, timeout=15)
        except (PiError, asyncio.TimeoutError):
            await self._kill()

    async def new_session(self) -> None:
        async with self._lock:
            await self._command({"type": "new_session"}, timeout=30)

    async def state(self) -> dict:
        return await self._command({"type": "get_state"}, timeout=30)

    async def _kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            await self._proc.wait()
        self._proc = None
        if self._settled and not self._settled.is_set():
            self._settled.set()

    async def close(self) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        await self._kill()
        for task in (self._reader, self._stderr):
            if task:
                task.cancel()
