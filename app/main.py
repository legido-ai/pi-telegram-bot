"""Entrypoint: Telegram polling plus the health endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from .bot import Bridge
from .config import Settings
from .health import serve
from .speech import Speech

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format='{"ts": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "msg": "%(message)s"}',
)
# httpx logs full request URLs at INFO, and Telegram puts the bot token in the
# path. Never let that reach the container log.
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("app")


async def run() -> None:
    settings = Settings.from_env()
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.session_dir, exist_ok=True)
    speech = Speech(settings)
    bridge = Bridge(settings, speech)

    app = Application.builder().token(settings.token).build()
    app.add_handler(CommandHandler("start", bridge.cmd_start))
    app.add_handler(CommandHandler("help", bridge.cmd_help))
    app.add_handler(CommandHandler("new", bridge.cmd_new))
    app.add_handler(CommandHandler("stop", bridge.cmd_stop))
    app.add_handler(CommandHandler("status", bridge.cmd_status))
    app.add_handler(CommandHandler("tts", bridge.cmd_tts))
    app.add_handler(CommandHandler("say", bridge.cmd_say))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE, bridge.on_voice))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, bridge.on_text))

    server = await serve(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    me = await app.bot.get_me()
    log.info("bot started as @%s", me.username)

    await stop.wait()
    log.info("shutting down")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    for session in bridge.sessions.values():
        await session.close()
    await speech.close()
    server.close()
    await server.wait_closed()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
