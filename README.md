# Pi Telegram Bot

Telegram bridge for [Pi](https://pi.dev) (`earendil-works/pi`), the same shape as
`opencode-telegram-bot`: one bot, one allowlisted user, one Pi session per
chat/topic, streamed answers, voice notes in and spoken replies out.

## Why a bridge

Pi has no HTTP server. It exposes an **RPC mode**: JSONL commands on stdin,
responses and session events on stdout. This bridge owns one `pi --mode rpc`
child process per conversation, so history lives in Pi's session files under
`/data/sessions` and survives restarts.

```text
Telegram ──long poll──► app/bot.py ──► app/pi.py (one child per chat/topic)
                              │                 │
                              │                 └── pi --mode rpc ──► OpenCode Go
                              ├──► speech-to-text  /v1/audio/transcriptions
                              └──► text-to-speech  /v1/audio/speech
```

## Configuration

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token (from the managed-bot flow) |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated numeric ids |
| `PI_PROVIDER` / `PI_MODEL` | Provider and model id in `models.json` |
| `PI_BASE_URL` | OpenAI-compatible base URL (`https://opencode.ai/zen/go/v1`) |
| `PI_API_KEY_ENV` | Name of the env var holding the key (`OPENCODE_GO_API_KEY`) |
| `STT_API_URL` / `STT_API_KEY` | Whisper-compatible endpoint |
| `TTS_API_URL` / `TTS_API_KEY` | OpenAI-compatible speech endpoint |
| `TTS_MODE` | `off`, `auto` (voice in → voice out) or `all` |

`models.json` is generated at container start by `entrypoint.sh`, so the key
never lands in the image.

## Commands

`/start`, `/help`, `/new`, `/stop`, `/status`, `/tts off|auto|all`.

## Local build

```bash
docker build -t pi:current .
```

## Icon

`assets/icon.svg` is the source; `assets/icon.png` (512×512) is what the bot
uses as its profile photo (`setMyProfilePhoto`).
