# Hermes Voice

A hands-free, always-listening voice assistant web app — like ChatGPT Voice Mode — that runs entirely on your own server and connects to [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Speak → Whisper STT → Hermes Agent → Piper TTS → plays back. No button presses. VAD detects speech automatically.

Works as a PWA from iPhone Safari over HTTPS.

<img width="1536" height="1024" alt="Hermes Voice" src="https://github.com/user-attachments/assets/e7037983-b23e-4d62-87b0-79268a4fa48b" />

---

## Features

- **Always-listening VAD** — adaptive noise floor calibration, no push-to-talk
- **Server-side STT** — faster-whisper (small model, int8, CPU-friendly)
- **Local TTS** — Piper (fully offline, low-latency, Italian voice included)
- **Pipelined TTS** — sentence N+1 is fetched while N is playing; first audio in ~1 s
- **iOS Safari compatible** — AudioContext unlock, correct `audio/mp4` MIME handling
- **Discord mirroring** — each voice session creates a Discord thread with full transcript
- **Zero frontend dependencies** — pure Web Audio API, no npm, no build step
- **Auto-restart** — systemd services keep everything running across reboots
- **Fixed HTTPS URL** — ngrok free tier with a permanent subdomain

---

## Architecture

```
iPhone (Safari PWA)
  │  HTTPS (ngrok tunnel)
  ▼
Flask server — port 5000
  ├── POST /transcribe ──► faster-whisper ──► text
  ├── POST /chat       ──► Hermes Agent (port 8642) ──► reply
  │                              │
  │                        OpenRouter API (cloud)
  │                        └── fallback: Ollama local (qwen2.5:3b)
  │                              │
  │                         (async) Discord thread mirror
  └── POST /tts        ──► Piper binary ──► WAV base64
```

**LLM fallback chain** — Hermes Agent tries models in order:
1. Primary cloud model via OpenRouter (e.g. `inclusionai/ling-2.6-1t:free`)
2. Local Ollama model (`qwen2.5:3b`) — kicks in automatically on rate limits or API errors

---

## Server Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 3+ cores (modern x86) |
| RAM | 4 GB | 6 GB |
| Disk | 10 GB | 30 GB |
| GPU | not required | not required |
| OS | Ubuntu 22.04+ | Ubuntu 22.04+ |

> The stack runs fine on a low-cost VPS (tested on AMD Ryzen 9 7950X3D, 3 vCPU, 6 GB RAM).

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/lorenzotelesco-png/hermes-voice
cd hermes-voice
pip install -r requirements.txt
```

### 2. Install Piper TTS

```bash
# Download Piper binary (Linux x86_64)
curl -L https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz | tar xz
sudo mv piper/piper /usr/local/bin/

# Download an Italian voice model
mkdir -p models/it_IT && cd models/it_IT
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/paola/medium/it_IT-paola-medium.onnx.json
cd ../..
```

Browse all available voices at [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).

### 3. Install and configure Hermes Agent

```bash
# Install Hermes Agent (follow official instructions)
# https://github.com/NousResearch/hermes-agent

# Required: enable the API server in ~/.hermes/.env
echo "API_SERVER_ENABLED=true" >> ~/.hermes/.env
echo "OPENROUTER_API_KEY=your_openrouter_key_here" >> ~/.hermes/.env
```

Configure `~/.hermes/config.yaml` — key settings:

```yaml
model:
  default: inclusionai/ling-2.6-1t:free   # or any OpenRouter model
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions

# Fallback: local Ollama when cloud model is rate-limited or unavailable
fallback_model:
  provider: openrouter
  model: qwen2.5:3b
  base_url: http://127.0.0.1:11434/v1
  api_key: ollama

discord:
  require_mention: false
  free_response_channels: ''
  allowed_channels: 'YOUR_CHANNEL_ID'   # channel where Hermes can respond
  auto_thread: true                      # new thread per conversation
  reactions: true
  channel_prompts:
    'YOUR_CHANNEL_ID': "Sei Hermes, l'assistente AI personale. Rispondi SEMPRE in italiano."

session_reset:
  mode: both
  idle_minutes: 1    # reset session after 1 min of inactivity → new thread each time
  at_hour: 4
```

### 4. Install Ollama and pull the fallback model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b    # ~1.9 GB — fits in 4 GB RAM
```

Ollama runs as a system service automatically after install.

### 5. Configure the voice server

```bash
cp .env.example .env
nano .env
```

Minimum required:

```env
PIPER_MODEL_PATH=models/it_IT/it_IT-paola-medium.onnx
PIPER_MODEL_CONFIG=models/it_IT/it_IT-paola-medium.onnx.json
STT_LANGUAGE=it
```

Optional Discord mirroring:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
DISCORD_BOT_TOKEN=your_bot_token_here
```

### 6. Set up systemd services (auto-restart on reboot)

```bash
# Copy service files
cp deploy/hermes-agent.service /etc/systemd/system/
cp deploy/hermes-voice.service /etc/systemd/system/
cp deploy/ngrok-tunnel.service /etc/systemd/system/   # optional

# Edit paths and keys in hermes-agent.service
nano /etc/systemd/system/hermes-agent.service

# Enable and start
systemctl daemon-reload
systemctl enable --now hermes-agent
systemctl enable --now hermes-voice
systemctl enable --now ngrok-tunnel   # optional
```

Check status:

```bash
systemctl status hermes-agent
systemctl status hermes-voice
journalctl -u hermes-voice -f
```

### 7. Expose over HTTPS (required for microphone on mobile)

**Option A — ngrok free tier (fixed permanent URL):**

```bash
# Install ngrok
curl -fsSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

# Authenticate (get your token at https://dashboard.ngrok.com)
ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN

# Claim a free fixed subdomain at https://dashboard.ngrok.com/domains
# Then update deploy/ngrok-tunnel.service with your domain and enable the service
```

**Option B — Cloudflare Tunnel (free, requires Cloudflare account):**

```bash
cloudflared tunnel --url http://127.0.0.1:5000
```

**Option C — reverse proxy (Caddy / nginx) with your own domain.**

### 8. Install as iOS PWA

1. Open the HTTPS URL in Safari on your iPhone
2. Tap the Share button → **Add to Home Screen**
3. Open the app from your home screen (full-screen, no browser chrome)
4. Tap anywhere to start — then just speak

---

## Discord Bot Setup

To enable Discord integration (auto-thread + voice mirroring):

1. Go to [discord.com/developers](https://discord.com/developers/applications) → New Application
2. Bot tab → Reset Token → copy the token
3. OAuth2 → URL Generator: scopes `bot`, permissions:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Read Message History
4. Invite the bot to your server
5. Create a webhook in your target channel: Channel Settings → Integrations → Webhooks
6. Add both values to `.env` (voice server) and `~/.hermes/.env` (Hermes Agent)

> **Note**: Hermes Agent handles Discord conversations directly (reads messages, creates threads via `auto_thread: true`). The voice server uses the webhook only to mirror voice session transcripts.

---

## Environment Variables

### Voice server (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPER_MODEL_PATH` | `models/it_IT/it_IT-paola-medium.onnx` | Piper ONNX model path |
| `PIPER_MODEL_CONFIG` | `models/it_IT/it_IT-paola-medium.onnx.json` | Piper model config path |
| `HERMES_API_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Hermes Agent API endpoint |
| `STT_LANGUAGE` | `it` | Whisper transcription language (BCP-47) |
| `DISCORD_WEBHOOK_URL` | *(disabled)* | Webhook URL for voice session mirroring |
| `DISCORD_BOT_TOKEN` | *(disabled)* | Bot token for Discord thread creation |
| `PORT` | `5000` | Flask server port |

### Hermes Agent (`~/.hermes/.env`)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key (get one at openrouter.ai) |
| `API_SERVER_ENABLED` | Must be `true` to expose the local API on port 8642 |
| `DISCORD_BOT_TOKEN` | Same bot token as above |

---

## Troubleshooting

**Microphone not working on iOS** — the app requires HTTPS. `http://` will silently fail. Use ngrok or Cloudflare Tunnel.

**Hermes returns errors** — check `journalctl -u hermes-agent -n 50`. If rate-limited, the Ollama fallback kicks in automatically. If Ollama isn't installed, install it with `curl -fsSL https://ollama.com/install.sh | sh && ollama pull qwen2.5:3b`.

**No Discord threads** — make sure `free_response_channels` is **empty** and the channel ID is in `allowed_channels` instead. Channels listed in `free_response_channels` disable auto-threading by design (Hermes source behavior).

**ngrok token rejected (ERR_NGROK_107)** — generate a fresh authtoken from the ngrok dashboard and run `ngrok config add-authtoken NEW_TOKEN`, then `systemctl restart ngrok-tunnel`.

**SSH unreachable after reboot** — if `ListenAddress` in `/etc/ssh/sshd_config` is set to a Tailscale or VPN IP, SSH will fail at boot before the network is ready. Change it to `0.0.0.0`.

---

## Security Notes

- **Never commit `.env`** — it's in `.gitignore`
- **Keep `~/.hermes/.env` private** — it contains your OpenRouter API key and Discord bot token
- The Flask server binds to `0.0.0.0:5000` — restrict it with a firewall if not behind a tunnel:
  ```bash
  ufw allow from 127.0.0.1 to any port 5000
  ```
- The Hermes Agent API (port 8642) is local-only by default — do not expose it publicly
- ngrok free tier URLs are public — anyone with the URL can use the voice interface; add authentication if needed

---

## License

MIT
