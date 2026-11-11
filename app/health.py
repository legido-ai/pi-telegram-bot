"""Tiny HTTP health endpoint for compose/operator checks."""

from __future__ import annotations

import asyncio
import logging

from .config import Settings

log = logging.getLogger("health")


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await reader.readline()
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        path = request.split(b" ")[1].decode() if b" " in request else "/"
        body = b"ok\n"
        if path in ("/healthz", "/health/live", "/health/ready", "/"):
            head = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
        else:
            head = b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n"
        writer.write(head + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()
    finally:
        writer.close()


async def serve(settings: Settings) -> asyncio.AbstractServer:
    server = await asyncio.start_server(_handle, settings.health_host, settings.health_port)
    log.info("health on %s:%s", settings.health_host, settings.health_port)
    return server
