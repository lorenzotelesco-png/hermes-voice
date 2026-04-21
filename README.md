# Hermes Voice

A hands-free, always-listening voice assistant web app - like ChatGPT Voice Mode - that runs entirely on your own server and connects to [Hermes Agent](https://github.com/opencode-ai/hermes).

Speak → Whisper STT → Hermes Agent → Piper TTS → plays back. No button presses. VAD detects speech automatically.

Works as a PWA from iPhone Safari over HTTPS (e.g. Cloudflare Tunnel).

![demo](docs/demo.png)

---

## Features

- **Always-listening VAD** - adaptive noise floor calibration, no push-to-talk
- **Server-side STT** - faster-whisper (small, int8, CPU-friendly)
- **Local TTS** - Piper (offline, low-latency, multiple languages/voices)
- **iOS Safari compatible** - AudioContext unlock pattern, correct MIME handling
- **Discord mirroring** - optionally mirrors every voice exchange to a Discord thread
- **Zero frontend dependencies** - pure Web Audio API, no npm, no build step

---

## Requirements

| Component | Notes |
|-----------|-------|
| Python 3.10+ | Server-side |
| [Hermes Agent](https://github.com/opencode-ai/hermes) | Must be running with `API_SERVER_ENABLED=true` |
| [Piper TTS](https://github.com/rhasspy/piper) | Binary in PATH + ONNX model |
| HTTPS | Required by browsers for microphone access - use Cloudflare Tunnel, Caddy, or nginx |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/your-username/hermes-voice
cd hermes-voice
pip install -r requirements.txt
```

### 2. Install Piper

```bash
# Linux x86_64
curl -L https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz | tar xz
sudo mv piper/piper /usr/local/bin/

# Download a voice model (example: Italian Paola medium)
mkdir -p models/it_IT
cd models/it_IT
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx.json
cd ../..
```

Browse all available voices at [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).

### 3. Enable Hermes Agent API server

In your Hermes `.env` file add:

```
API_SERVER_ENABLED=true
```

Then restart the Hermes gateway. The API will be available at `http://127.0.0.1:8642`.

### 4. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

Minimum required config:

```env
PIPER_MODEL_PATH=models/it_IT/it_IT-paola-medium.onnx
PIPER_MODEL_CONFIG=models/it_IT/it_IT-paola-medium.onnx.json
STT_LANGUAGE=it
```

### 5. Expose over HTTPS (required for microphone on mobile)

**Cloudflare Tunnel (free, no account needed for testing):**

```bash
cloudflared tunnel --url http://127.0.0.1:5000
# Prints a trycloudflare.com URL — open it on your phone
```

**Or use your own domain with Caddy/nginx.**

### 6. Run

```bash
python server/app.py
```

Open the HTTPS URL on your iPhone, tap once to start, then speak.

---

## Discord Mirroring (optional)

When enabled, each voice session creates a Discord thread and mirrors every exchange:

```
🎤 Lorenzo: ciao, che tempo fa oggi?
Hermes: Ciao! Non ho accesso ai dati meteo in tempo reale, ma...
```

To enable:

1. **Create a webhook** in your Discord channel:  
   Channel Settings → Integrations → Webhooks → New Webhook → Copy URL

2. **Give the bot permissions** in that channel:  
   `Send Messages`, `Create Public Threads`, `Send Messages in Threads`

3. **Add to `.env`:**

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
DISCORD_BOT_TOKEN=your_bot_token_here
```

---

## Architecture

```
iPhone (Safari PWA)
  │
  │  HTTPS
  ▼
Flask server (port 5000)
  ├── POST /transcribe  →  faster-whisper  →  text
  ├── POST /chat        →  Hermes Agent API (port 8642)  →  reply
  │                              │
  │                              └── (async) Discord thread mirror
  └── POST /tts         →  Piper binary  →  WAV base64
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPER_MODEL_PATH` | `models/it_IT/it_IT-paola-medium.onnx` | Path to Piper ONNX model |
| `PIPER_MODEL_CONFIG` | `models/it_IT/it_IT-paola-medium.onnx.json` | Path to model config |
| `HERMES_API_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Hermes Agent API endpoint |
| `STT_LANGUAGE` | `it` | Whisper transcription language |
| `DISCORD_WEBHOOK_URL` | *(disabled)* | Discord webhook for mirroring |
| `DISCORD_BOT_TOKEN` | *(disabled)* | Discord bot token for thread creation |
| `PORT` | `5000` | Flask server port |

---

## Security Notes

- **Never commit `.env`** — it's in `.gitignore` by default
- Model files are also gitignored (large binaries, download separately)
- Discord mirroring is opt-in and disabled by default
- The Flask server binds to `0.0.0.0` — put it behind a reverse proxy in production, or restrict with a firewall to only accept traffic from your tunnel

---

## License

MIT
