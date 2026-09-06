# Hermes Voice

A hands-free, always-listening voice assistant web app — like ChatGPT Voice Mode — that runs entirely on your own server and connects to [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Speak → Whisper STT → Hermes Agent → Piper TTS → plays back. No button presses. VAD detects speech automatically.

Works as a PWA from iPhone Safari over HTTPS.

<img width="1536" height="1024" alt="Hermes Voice" src="https://raw.githubusercontent.com/lorenzotelesco-png/hermes-voice/master/assets/hermes-showcase.svg" />

---

## Features

- **Always-listening VAD** — adaptive noise floor calibration, no push-to-talk
- **Speech I/O delegated to Hermes** — STT and TTS run on the Hermes dashboard, so
  providers, models and voices are configured once in `config.yaml` and shared with
  every other Hermes surface. This server holds no speech stack of its own.
- **Streaming replies** — the reply is spoken as it is written, sentence by
  sentence, instead of after the model has finished. Synthesis for sentence N+1
  overlaps playback of N.
- **Barge-in** — the mic stays live while Hermes speaks; start talking and playback
  stops mid-sentence. The agent is told what it actually managed to say, so it
  does not carry on as though the whole reply had landed.
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
Flask server — port 5000          (thin proxy + static PWA, no speech stack)
  ├── POST /transcribe ──► Hermes dashboard :9119 /api/audio/transcribe ──► text
  ├── POST /chat  (SSE) ─► Hermes Agent     :8642 /v1/chat/completions  ──► reply
  │                         streamed; sentences are cut server-side and pushed
  │                         to the client one at a time as the model writes
  │                              │
  │                        OpenRouter (deepseek-v4-flash-0731)
  │                              │
  │                         (async) Discord thread mirror
  └── POST /tts        ──► Hermes dashboard :9119 /api/audio/speak      ──► audio
```

Both dashboard calls authenticate with `X-Hermes-Session-Token`; the dashboard is
bound to loopback, so nothing but this server can reach it.

**Where things are configured** — this server decides almost nothing:

| Concern | Configured in |
|---------|---------------|
| LLM, reasoning effort | `~/.hermes/config.yaml` → `model`, `agent.reasoning_effort` |
| STT provider, language | `~/.hermes/config.yaml` → `stt` |
| TTS provider, voice | `~/.hermes/config.yaml` → `tts` |
| Endpointing, VAD, barge-in | `web/app.js` (client-side) |

**Client tuning** (top of `web/app.js`):

All tunable live from the phone via query string, no redeploy — e.g.
`?silence=1200&cont=1.2`.

| Constant | Query | Default | What it does |
|----------|-------|---------|--------------|
| `SILENCE_MS` | `silence` | 900 | Pause before a turn is considered over |
| `START_MULT` | `start` | 2.8 | Bar to **open** a turn, as a multiple of the calibrated noise floor |
| `CONTINUE_MULT` | `cont` | 1.35 | Bar to **stay** in a turn. Must be well below `START_MULT`: speech dips constantly between words, and a single bar reads every dip as the end of the sentence |
| `MIN_UTTERANCE_MS` | `minms` | 500 | Bursts shorter than this are discarded as noise |
| `BARGE_IN_MULT` | — | 4.0 | Speech trigger during playback. Lower = easier to interrupt, more likely to self-trigger |
| `BARGE_IN_GRACE_MS` | — | 500 | Dead period after audio starts, so the reply cannot interrupt itself |

**If it cuts you off mid-sentence:** raise `silence`, then lower `cont`. Cutting
someone off costs a whole retry, which is far more expensive than the few hundred
milliseconds a longer pause costs.

**Where the time goes:** add `?debug=1` and a timing strip appears at the bottom
of the screen, marking each phase from the end of your speech:

```
fine-voce 0.00  stt 1.42  frase1 3.10  primo-suono 3.75  frase2 3.81
```

Read it like this — `stt` is transcription, the gap from there to `frase1` is the
model, and `primo-suono` minus `frase1` is speech synthesis. **If every `fraseN`
lands at nearly the same time, the reply was not streamed**: something between
the server and the phone buffered the whole response. That is a different fault
from a slow model and needs a different fix.

**Ending a turn on purpose:** tapping mute while you are talking submits what you
have said so far. No VAD is right every time — this is the deterministic override
for a long pause the detector would otherwise cut into.

Barge-in depends on the browser's echo cancellation (requested via
`getUserMedia`). On a phone at speaker volume without it, the mic hears the reply
and cuts it off immediately — if that happens, raise `BARGE_IN_MULT`.

---

## Server Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 1 core | 2+ cores |
| RAM | 2 GB | 4 GB |
| Disk | 5 GB | 20 GB |
| GPU | not required | not required |
| OS | Ubuntu 22.04+ | Ubuntu 22.04+ |

> Requirements dropped once STT and TTS moved to the Hermes dashboard: this server no
> longer keeps a Whisper model resident (~1 GB) or shells out to Piper. Sizing is now
> driven by Hermes itself, not by this process.

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/lorenzotelesco-png/hermes-voice
cd hermes-voice
pip install -r requirements.txt
```

### 2. Enable the Hermes dashboard (speech in/out)

STT and TTS are served by the dashboard, which needs the `web` extra:

```bash
cd ~/.hermes/hermes-agent && uv pip install -e ".[web]"
```

Pick a **fixed** session token — without it the dashboard mints a random one at
every start and this server gets 401 after each restart:

```bash
echo "HERMES_DASHBOARD_SESSION_TOKEN=$(openssl rand -base64 32)" >> ~/.hermes/.env
```

Piper needs no manual download any more: Hermes installs it via `hermes tools` →
Voice & TTS → Piper (or `pip install piper-tts`) and fetches the voice model on
first use into `~/.hermes/cache/piper-voices/`. Browse voices at
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) — 44 languages.

### 3. Install and configure Hermes Agent

```bash
# Install Hermes Agent (follow official instructions)
# https://github.com/NousResearch/hermes-agent

# Required: enable the API server in ~/.hermes/.env
echo "API_SERVER_ENABLED=true" >> ~/.hermes/.env
echo "API_SERVER_KEY=$(openssl rand -base64 32)" >> ~/.hermes/.env
echo "OPENROUTER_API_KEY=your_openrouter_key_here" >> ~/.hermes/.env
echo "DEEPINFRA_API_KEY=your_deepinfra_key_here" >> ~/.hermes/.env
```

Configure `~/.hermes/config.yaml` — key settings:

```yaml
model:
  default: deepseek/deepseek-v4-flash-0731
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  api_mode: chat_completions

agent:
  # Disable thinking outright. Measured time to the first SPEAKABLE token
  # (delta.content, which is what TTS can actually say):
  #
  #   reasoning off      ~1.1 s
  #   reasoning default  ~5.6 s
  #   reasoning "low"    ~9.9 s
  #
  # "low" is the lowest effort this route accepts, but it is not "little
  # thinking": it still emitted 130-360 reasoning chunks before any content.
  # For a voice turn every one of those is silence. Lowest-available-effort and
  # disabled are different switches — this needs the second one.
  reasoning_effort: none

provider_routing:
  # Default is "price", which routes to the cheapest provider regardless of how
  # slow it is. Voice cares about time-to-first-token.
  sort: latency

stt:
  provider: deepinfra          # whisper-large-v3-turbo, ~$0.0002/min
  language: it                 # pinned: kills auto-detect latency and misdetection

tts:
  provider: piper              # local, no network hop — fastest time-to-first-word
  piper:
    voice: it_IT-paola-medium

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

### 4. Configure the voice server

```bash
cp .env.example .env
nano .env
```

Minimum required:

```env
HERMES_API_KEY=<same value as API_SERVER_KEY in ~/.hermes/.env>
HERMES_DASHBOARD_TOKEN=<same value as HERMES_DASHBOARD_SESSION_TOKEN>
```

Both must match their counterparts on the Hermes side exactly, or the server
answers 401. Speech settings (provider, voice, language) are **not** here — they
live in `~/.hermes/config.yaml`.

Optional Discord mirroring:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
DISCORD_BOT_TOKEN=your_bot_token_here
```

### 5. Set up systemd services (auto-restart on reboot)

```bash
# Copy service files
cp deploy/hermes-agent.service /etc/systemd/system/
cp deploy/hermes-dashboard.service /etc/systemd/system/
cp deploy/hermes-voice.service /etc/systemd/system/
cp deploy/ngrok-tunnel.service /etc/systemd/system/   # optional

# Edit paths and keys in hermes-agent.service
nano /etc/systemd/system/hermes-agent.service

# Enable and start
systemctl daemon-reload
systemctl enable --now hermes-agent
systemctl enable --now hermes-dashboard
systemctl enable --now hermes-voice
systemctl enable --now ngrok-tunnel   # optional
```

Check status:

```bash
systemctl status hermes-agent
systemctl status hermes-voice
journalctl -u hermes-voice -f
```

### 6. Expose over HTTPS (required for microphone on mobile)

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

### 7. Install as iOS PWA

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
| `HERMES_DASHBOARD_URL` | `http://127.0.0.1:9119` | Hermes dashboard, serves STT and TTS |
| `HERMES_DASHBOARD_TOKEN` | *(required)* | Must equal `HERMES_DASHBOARD_SESSION_TOKEN` on the dashboard |
| `HERMES_API_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Hermes Agent API endpoint |
| `HERMES_API_KEY` | *(required)* | Bearer token — must equal `API_SERVER_KEY` in `~/.hermes/.env` |
| `HERMES_MODEL` | `hermes-agent` | Model name advertised by Hermes on `/v1/models` |
| `HERMES_MAX_TOKENS` | `800` | Max tokens per reply |
| `DISCORD_WEBHOOK_URL` | *(disabled)* | Webhook URL for voice session mirroring |
| `DISCORD_BOT_TOKEN` | *(disabled)* | Bot token for Discord thread creation |
| `PORT` | `5000` | Flask server port |

### Hermes Agent (`~/.hermes/.env`)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key (get one at openrouter.ai) |
| `API_SERVER_ENABLED` | Must be `true` to expose the local API on port 8642 |
| `API_SERVER_KEY` | **Required.** Bearer token for the API server — Hermes rejects every request without it, loopback included. Mirror it into this repo's `.env` as `HERMES_API_KEY` |
| `DEEPINFRA_API_KEY` | DeepInfra key — used for STT (`stt.provider: deepinfra`) |
| `DISCORD_BOT_TOKEN` | Same bot token as above |

---

## Troubleshooting

**Microphone not working on iOS** — the app requires HTTPS. `http://` will silently fail. Use ngrok or Cloudflare Tunnel.

**Hermes returns errors** — check `journalctl -u hermes-agent -n 50`.

**A question that needs the web takes 15s+** — check `web.backend` in `config.yaml`. Left empty, Hermes falls back to its keyless ring (Exa/Parallel/Firecrawl/Keenable) and retries across vendors on every rate limit, so one search costs several wasted round trips. Setting a keyed backend cut the average from 15.3s to 8.6s here, and the worst case from 29s to 12s — **an API key already sitting in `.env` is not used unless `web.backend` names that provider**:

```yaml
web:
  backend: "tavily"
  search_backend: "tavily"
  extract_backend: "tavily"
```

**Nothing is listening on 8642** — `hermes gateway` is a command group. The unit file must run `hermes gateway run --replace`; plain `hermes gateway` binds nothing.

**"Gateway already running"** — a second gateway is up, typically a *user* unit (`systemctl --user status hermes-gateway`) alongside the system one. Pick one and disable the other, or they fight over the port on every boot.

**`/chat` returns 401/403** — `HERMES_API_KEY` in this repo's `.env` must equal `API_SERVER_KEY` in `~/.hermes/.env`. Hermes requires this token on every deployment, loopback included.

**`/transcribe` or `/tts` returns 401** — `HERMES_DASHBOARD_TOKEN` must equal `HERMES_DASHBOARD_SESSION_TOKEN` in the dashboard's environment. If that variable was never set, the dashboard picked a random token at boot: set it in `~/.hermes/.env`, then `systemctl restart hermes-dashboard`.

**`/transcribe` or `/tts` returns 503** — the dashboard is not running. `systemctl status hermes-dashboard`, and check the `web` extra is installed.

**No audio comes back** — the TTS provider is failing on the Hermes side, not here. Check `tts.provider` in `~/.hermes/config.yaml` and `journalctl -u hermes-dashboard -n 50`.

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
