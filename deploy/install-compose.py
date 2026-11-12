SERVICE = """
  pi:
    # Pi has no HTTP server: this bridge owns one `pi --mode rpc` child per
    # chat/topic (see pi/README.md).
    image: ${PI_BOT_IMAGE:-pi:current}
    container_name: pi
    hostname: pi
    restart: unless-stopped
    init: true
    networks: [default]
    environment:
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN_PI:?TELEGRAM_BOT_TOKEN_PI missing in repo .env}
      TELEGRAM_ALLOWED_USER_IDS: ${TELEGRAM_ALLOWED_USER_ID:?TELEGRAM_ALLOWED_USER_ID missing in repo .env}
      TELEGRAM_TRIGGER: mention
      OPENCODE_GO_API_KEY: ${OPENCODE_GO_API_KEY:?OPENCODE_GO_API_KEY missing in repo .env}
      PI_BASE_URL: https://opencode.ai/zen/go/v1
      PI_PROVIDER: opencode-go
      PI_MODEL: deepseek-v4.1-flash
      PI_API_KEY_ENV: OPENCODE_GO_API_KEY
      DATA_DIR: /data
      WORKSPACE_DIR: /workspace
      HEALTH_HOST: 127.0.0.1
      HEALTH_PORT: "8080"
      # Shared local speech stack on box_default.
      STT_API_URL: http://speech-to-text:9000/v1
      STT_API_KEY: local
      STT_MODEL: small
      STT_LANGUAGE: es
      # Local Piper voices; voice notes get voice answers by default.
      TTS_API_URL: http://text-to-speech:9000/v1
      TTS_API_KEY: local
      TTS_MODEL: piper
      TTS_VOICE: es-ES-ElviraNeural
      TTS_MODE: off
    volumes:
      - pi-data:/data
      - pi-workspace:/workspace
    healthcheck:
      test: ["CMD-SHELL", "python -c \\"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)\\""]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s
"""

VOLUMES = """  pi-data: {name: pi-data, external: true}
  pi-workspace: {name: pi-workspace, external: true}
"""

import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/srv/work/agentic-architecture/box/compose.yml")
text = path.read_text()

if "container_name: pi\n" in text:
    print("compose already has the pi service; nothing to do")
    sys.exit(0)

marker = "\nvolumes:\n"
index = text.rindex(marker)
text = text[:index] + "\n" + SERVICE.strip("\n") + text[index:]
for line in VOLUMES.strip("\n").splitlines():
    name = line.split(",")[0].strip()
    if name not in text:
        text = text.rstrip("\n") + "\n" + line
text = text.rstrip("\n") + "\n"
path.write_text(text)
print("compose updated")
