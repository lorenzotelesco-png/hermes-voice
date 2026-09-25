# Hermes Hub (formerly Hermes Voice)

One phone app for [Hermes Agent](https://github.com/NousResearch/hermes-agent),
running on your own server: talk or type in the same conversation, read the
transcript of every channel Hermes is on, answer its approval requests, and
watch and fix the server — with a push notification when something breaks.
Files and an inbox of your chats come next — the plan is in
[docs/ROADMAP.md](docs/ROADMAP.md).

Voice is hands-free, like ChatGPT Voice Mode: speak → Whisper STT → Hermes → TTS,
no button presses, VAD detects speech automatically.

Works as a PWA from iPhone Safari over HTTPS.

<img width="1536" height="1024" alt="Hermes Voice" src="https://raw.githubusercontent.com/lorenzotelesco-png/hermes-voice/master/assets/hermes-showcase.svg" />

---

## Features

- **Voice mode** — full screen, opened from the button next to the text field as
  in ChatGPT; closing it shows the chat with the transcript. Four blobs follow
  four voice bands: your voice while it listens, Hermes' voice while it speaks.
  Thinking moves differently and listens to nothing. Each state has its colors.
- **Always-listening VAD** — adaptive noise floor calibration, no push-to-talk
- **Speech I/O delegated to Hermes** — STT and TTS run on the Hermes dashboard, so
  providers, models and voices are configured once in `config.yaml` and shared with
  every other Hermes surface. This server holds no speech stack of its own.
- **Streaming replies** — the reply is spoken as it is written, sentence by
  sentence, instead of after the model has finished. Synthesis for sentence N+1
  overlaps playback of N.
- **Barge-in** — the mic stays live while Hermes speaks; start talking and playback
  stops mid-sentence, and a reply still being written is stopped.
- **One conversation, voice and text** — a spoken turn is a message in the same
  Hermes session as the typed ones, with its transcript in the thread. Hermes keeps
  the whole history server-side, not the last few messages.
- **Every channel's transcripts** — conversations from the app, Discord, the CLI
  and cron in one list, with full-text search; any of them can be continued.
- **Approvals on the phone** — when Hermes wants to run a command that needs a
  yes, a sheet shows the exact command with Approve / Deny.
- **Turns survive the app leaving the screen** — iOS drops the connection; the
  hub keeps reading the turn and the app picks it up where it left off.
- **Server tab** — services with state, uptime and memory, RAM/disk/load, Hermes'
  gateway, cost today and over 7 days; logs (Hermes' files and the services'
  journals) with filters; cron jobs; restart a service with one confirmed tap.
- **Push alerts** — a service down for 2 minutes, restarts by systemd (OOM
  named), disk over 85%, RAM under 300 MB, failed cron runs. They go through
  Apple's push service, so an alert that the tunnel is down still arrives.
- **iOS Safari compatible** — AudioContext unlock, correct `audio/mp4` MIME handling
- **App shell with tabs** — Chat, Server, File, Altro; a voice session or a
  running turn keeps going while you look at another tab
- **Small frontend** — Preact + TypeScript built with Vite (~36 KB gzipped JS,
  Markdown included), fonts served by the hub itself, no third-party requests
- **Auto-restart** — systemd services keep everything running across reboots
- **Fixed HTTPS URL** — ngrok free tier with a permanent subdomain

---

## Architecture

```
iPhone (Safari PWA)
  │  HTTPS (ngrok tunnel)
  ▼
Hub — FastAPI on 127.0.0.1:5000   (thin proxy + the built app, no speech stack)
  ├── GET  /api/voice-config ► Hermes dashboard :9119 /api/audio/voice-config
  │                             so the phone can talk to the STT provider itself
  ├── POST /api/transcribe ──► Hermes dashboard :9119 /api/audio/transcribe ──► text
  │                             (fallback only — see client-direct below)
  ├── POST /api/chat (SSE) ──► Hermes API server :8642 /api/sessions/{id}/chat/stream
  │                             one turn on a Hermes session; for a voice turn the
  │                             sentences are cut server-side and pushed as written
  ├── GET  /api/runs/{id}/events ► the same turn again, from the last event seen
  ├── POST /api/runs/{id}/approval | stop ► Hermes :8642 /v1/runs/{id}/…
  ├── GET  /api/sessions[/{id}] ► Hermes :8642 session list and transcript
  ├── GET  /api/sessions/search ► Hermes dashboard :9119 full-text search
  ├── POST /api/tts        ──► Hermes dashboard :9119 /api/audio/speak      ──► audio
  ├── GET  /api/server/*   ──► systemctl show, /proc (read without privileges),
  │                            dashboard logs / cron / usage / status
  ├── POST …/restart, GET logs of a service ──► hermes-hub-control (root helper)
  └── POST /api/push/*     ──► subscriptions; alerts go out via Apple's push service
      (background)  the watcher: every 30 s, services, disk, RAM, cron → push
```

Both dashboard calls authenticate with `X-Hermes-Session-Token`; the dashboard is
bound to loopback, so nothing but this server can reach it. Every call to Hermes
goes through `server/hub/hermes.py`, and `scripts/contract_check.py` checks that
each endpoint the hub uses still answers in the expected shape — run it before
and after `hermes update`.

| Path | What lives there |
|------|------------------|
| `server/hub/` | The backend: `main.py` routes, `security.py` access gate, `hermes.py` Hermes clients, `runs.py` turns that outlive the connection, `transcript.py` what the phone is shown of a session, `speech.py` sentence cutting, `audit.py` action log |
| `server/hub/system.py`, `monitor.py` | What systemd and /proc say, and the alert rules |
| `server/hub/control.py`, `deploy/control/` | Restarts and service journals, through the root-side helper |
| `server/hub/push.py`, `webpush.py` | Push subscriptions, and the RFC 8291 / VAPID sender |
| `web/src/server/` | The Server tab: overview, logs, cron |
| `web/src/chat/` | The Chat tab: `store.ts` the conversation on screen, thread, conversation list, approval sheet, voice dock |
| `web/src/voice/engine.ts` | The voice pipeline: VAD, STT, streamed speech, barge-in |
| `web/src/tabs/` | The other tabs |
| `deploy/` | systemd units and `deploy.sh` |

### Conversations, turns and approvals

- **Sessions are Hermes'.** The hub sends only the new message; Hermes keeps the
  history, names the conversation and lists it with every other channel's. The
  long-term memory scope (`X-Hermes-Session-Key: hermes-voice:pwa`) is the same
  for every conversation from the app, and unchanged from the voice-only app.
- **A turn outlives the connection.** The hub reads Hermes' stream into a run
  (`server/hub/runs.py`) and the phone follows it. Relaying the stream directly
  would have passed every iOS disconnect upstream, and Hermes interrupts a turn
  whose client goes away. A finished run is kept 10 minutes for late readers.
- **Approvals.** `approval.request` events become a sheet with the exact command
  (redacted by Hermes). The phone can approve once, approve similar commands for
  the rest of that reply (Hermes scopes "session" to the run on this endpoint),
  or deny. "Always" is never offered: it would change Hermes' config for every
  channel. Unanswered, Hermes refuses the command by itself at `approvals.timeout`.
  Every answer and every stop goes to the audit log with the command.
- **Voice turns** carry a per-turn instruction for speakable replies; typed turns
  in the same conversation do not, so they may use Markdown.

### The Server tab, restarts and alerts

- **Reading needs no privileges.** Service state comes from `systemctl show`,
  RAM/disk/load from /proc, all inside the unprivileged hub. Hermes' version,
  gateway, logs, cron and costs come from the dashboard; if it is down the page
  says so and still shows the rest.
- **Changing something goes through a root helper.** The hub runs with
  `NoNewPrivileges`, so it cannot sudo, and polkit on Ubuntu 22.04 cannot grant
  "these units only". `hermes-hub-control.socket` listens on
  `/run/hermes-hub-control.sock` (group `hermes-hub` only) and starts
  `/usr/local/libexec/hermes-hub-control` as root per request. It checks the
  caller is the `hermes-hub` user and keeps its own allowlists (restart:
  Hermes, dashboard, ngrok, WARP; journal: those plus the hub and
  Tailscale). `deploy.sh` installs it from the commit fetched from GitHub, not
  from the working tree, which the hub can write. Every restart and cron action
  is in the audit log.
- **Alerts** are checked every 30 s by the hub itself, whether or not the app is
  open, and sent once when a problem starts and once when it ends. The first
  look after a restart of the hub only sets a baseline.
- **Push on iPhone** needs the app opened from the home screen (iOS 16.4+):
  Server › Notifiche › "Avvisi sul telefono". The sender is written on top of
  `cryptography` (no pywebpush) and tested byte for byte against RFC 8291's
  example. The VAPID key lives in `/var/lib/hermes-hub/vapid.pem`; replacing it
  orphans every subscription.

**Where things are configured** — this server decides almost nothing:

| Concern | Configured in |
|---------|---------------|
| LLM, reasoning effort | `~/.hermes/config.yaml` → `model`, `agent.reasoning_effort` |
| STT provider, language | `~/.hermes/config.yaml` → `stt` |
| TTS provider, voice | `~/.hermes/config.yaml` → `tts` |
| Endpointing, VAD, barge-in | `web/src/voice/engine.ts` (client-side) |

**Client tuning** (top of `web/src/voice/engine.ts`):

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

**Where the time goes:** turn on "Tempi sullo schermo" in the Altro tab (or add
`?debug=1`) and a timing strip appears at the top of the voice screen, marking each phase
from the end of your speech:

```
fine-voce 0.00  stt 1.42  frase1 3.10  primo-suono 3.75  frase2 3.81
```

Read it like this — `stt` is transcription, the gap from there to `frase1` is the
model, and `primo-suono` minus `frase1` is speech synthesis. **If every `fraseN`
of a long reply lands at nearly the same time, the reply was not streamed**:
something between the server and the phone buffered the whole response. That is
a different fault from a slow model and needs a different fix. A short reply is
no evidence either way — the model writes a dozen words in a few tens of
milliseconds, so its sentences arrive together even when nothing buffers.

To split the `frase1` gap between Hermes and the network, compare with the
server's own log: `agent.log` records when the turn started, when the model call
went out, and the model's `latency=`.

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

The hub runs as its own unprivileged user out of `/opt/hermes-hub`:

```bash
useradd --system --home-dir /var/lib/hermes-hub --create-home --shell /usr/sbin/nologin hermes-hub
git clone https://github.com/lorenzotelesco-png/hermes-voice /opt/hermes-hub
chown -R hermes-hub:hermes-hub /opt/hermes-hub
cd /opt/hermes-hub
sudo -u hermes-hub python3 -m venv venv
sudo -u hermes-hub venv/bin/pip install -r requirements.txt
cd web && sudo -u hermes-hub npm ci && sudo -u hermes-hub npm run build   # Node 20.19+ or 22.12+
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

### 4. Configure the hub

```bash
cp .env.example .env
chown hermes-hub:hermes-hub .env && chmod 600 .env
nano .env
```

Minimum required:

```env
VOICE_AUTH_TOKEN=<openssl rand -hex 32>
HERMES_API_KEY=<same value as API_SERVER_KEY in ~/.hermes/.env>
HERMES_DASHBOARD_TOKEN=<same value as HERMES_DASHBOARD_SESSION_TOKEN>
```

Both must match their counterparts on the Hermes side exactly, or the server
answers 401. Speech settings (provider, voice, language) are **not** here — they
live in `~/.hermes/config.yaml`.

### 5. Set up systemd services (auto-restart on reboot)

```bash
# Copy service files
cp deploy/hermes-agent.service /etc/systemd/system/
cp deploy/hermes-dashboard.service /etc/systemd/system/
cp deploy/hermes-hub.service /etc/systemd/system/
cp deploy/ngrok-tunnel.service /etc/systemd/system/   # optional

# Edit paths and keys in hermes-agent.service
nano /etc/systemd/system/hermes-agent.service

# Enable and start
systemctl daemon-reload
systemctl enable --now hermes-agent
systemctl enable --now hermes-dashboard
systemctl enable --now hermes-hub
systemctl enable --now ngrok-tunnel   # optional
```

Check status:

```bash
systemctl status hermes-agent
systemctl status hermes-hub
journalctl -u hermes-hub -f
python3 /opt/hermes-hub/scripts/contract_check.py
```

To update later, as root: `/opt/hermes-hub/deploy/deploy.sh` — it pulls, installs,
builds the app, restarts the hub and runs the contract check.

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
4. Type, or tap the microphone and just speak

---

## Discord Bot Setup

For Hermes' own Discord channel (its conversations then show up in the hub's
list like any other):

1. Go to [discord.com/developers](https://discord.com/developers/applications) → New Application
2. Bot tab → Reset Token → copy the token
3. OAuth2 → URL Generator: scopes `bot`, permissions:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Read Message History
4. Invite the bot to your server
5. Add the token to `~/.hermes/.env` as `DISCORD_BOT_TOKEN`

The hub used to mirror voice sessions into Discord threads. It no longer does:
the transcripts are in the app.

---

## Environment Variables

### Hub (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICE_AUTH_TOKEN` | *(required)* | Access token. The server refuses everything with 503 until it is set |
| `HUB_DB` | `hub.db` in the repo | SQLite file for the audit log (the systemd unit puts it in `/var/lib/hermes-hub`) |
| `HERMES_DASHBOARD_URL` | `http://127.0.0.1:9119` | Hermes dashboard, serves STT and TTS |
| `HERMES_DASHBOARD_TOKEN` | *(required)* | Must equal `HERMES_DASHBOARD_SESSION_TOKEN` on the dashboard |
| `HERMES_API_URL` | `http://127.0.0.1:8642` | Hermes API server. A full `…/v1/chat/completions` URL from older setups also works: only the origin is used |
| `HERMES_API_KEY` | *(required)* | Bearer token — must equal `API_SERVER_KEY` in `~/.hermes/.env` |
| `HUB_SERVICES` | Hermes, dashboard, hub, ngrok, Tailscale, WARP | Services shown and watched (comma-separated unit names) |
| `HUB_RESTARTABLE` | Hermes, dashboard, ngrok, WARP | Which of them get a restart button; the root helper has its own list and the last word |

The port (5000) is set in `deploy/hermes-hub.service`, not here.

### Hermes Agent (`~/.hermes/.env`)

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key (get one at openrouter.ai) |
| `API_SERVER_ENABLED` | Must be `true` to expose the local API on port 8642 |
| `API_SERVER_KEY` | **Required.** Bearer token for the API server — Hermes rejects every request without it, loopback included. Mirror it into this repo's `.env` as `HERMES_API_KEY` |
| `DEEPINFRA_API_KEY` | DeepInfra key — used for STT (`stt.provider: deepinfra`) |
| `DISCORD_BOT_TOKEN` | Bot token for Hermes' Discord channel |

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

**`/api/chat` returns 502 mentioning `HERMES_API_KEY`** — `HERMES_API_KEY` in this repo's `.env` must equal `API_SERVER_KEY` in `~/.hermes/.env`. Hermes requires this token on every deployment, loopback included.

**`/api/transcribe` or `/api/tts` returns 502 mentioning the session token** — `HERMES_DASHBOARD_TOKEN` must equal `HERMES_DASHBOARD_SESSION_TOKEN` in the dashboard's environment. If that variable was never set, the dashboard picked a random token at boot: set it in `~/.hermes/.env`, then `systemctl restart hermes-dashboard`.

**`/api/transcribe` or `/api/tts` returns 503** — the dashboard is not running. `systemctl status hermes-dashboard`, and check the `web` extra is installed.

**The app shows an old version** — "Altro" shows the commit and build time. `index.html` is never cached, so a stale version means the build did not run: `cd web && npm run build`, or use `deploy/deploy.sh`.

**"Il controllo dei servizi non è installato"** — the root helper is missing: run `deploy/deploy.sh`, which installs `hermes-hub-control.socket`. Check with `systemctl status hermes-hub-control.socket`.

**No push notification on the iPhone** — it only works in the app opened from the home screen, on iOS 16.4 or later, with notifications allowed in Settings › Notifications › Hermes. "Invia una notifica di prova" in the Server tab says how many devices accepted it; `journalctl -u hermes-hub | grep PUSH` shows what Apple answered.

**Something broke after `hermes update`** — `python3 scripts/contract_check.py` names the endpoint and the missing field.

**No audio comes back** — the TTS provider is failing on the Hermes side, not here. Check `tts.provider` in `~/.hermes/config.yaml` and `journalctl -u hermes-dashboard -n 50`.

**No Discord threads** — make sure `free_response_channels` is **empty** and the channel ID is in `allowed_channels` instead. Channels listed in `free_response_channels` disable auto-threading by design (Hermes source behavior).

**ngrok token rejected (ERR_NGROK_107)** — generate a fresh authtoken from the ngrok dashboard and run `ngrok config add-authtoken NEW_TOKEN`, then `systemctl restart ngrok-tunnel`.

**SSH unreachable after reboot** — if `ListenAddress` in `/etc/ssh/sshd_config` is set to a Tailscale or VPN IP, SSH will fail at boot before the network is ready. Change it to `0.0.0.0`.

---

## Scoping the voice channel's tools

The API server should not carry the CLI's toolset. Give it its own in
`~/.hermes/config.yaml` — CLI and Discord keep theirs:

```yaml
platform_toolsets:
  api_server:
    - web            # web_search stays: it must still fire on its own
    - memory
    - session_search
    - skills
    - cronjob
```

**Do this for access, not for speed.** It removes `terminal`, `process`,
`read_file`, `write_file`, `patch`, `search_files` and every `browser_*` tool
from a channel reachable over the public tunnel, which is the whole point.

The prompt does shrink — 12858 tokens to 8168, about 36% — and that buys
**nothing measurable**. Timed with alternating A/B blocks, 12 samples each:
median 1288ms reduced vs 1159ms full, against a standard deviation of 712ms.
Two blocks of the *same* configuration differed by 383ms, so within-arm drift
dwarfs the difference. Prefill is essentially free on this path; the latency
lives elsewhere.

## Client-direct speech-to-text

Audio used to make four hops — phone, tunnel, this server, dashboard, provider.
Measured from a phone that cost ~3.4s against ~1.4s of actual transcription: most
of it was carriage, not inference.

When the configured STT provider is reachable from a browser, the phone now uploads
straight to it and only the transcript comes back. `GET /api/voice-config` proxies the
dashboard's resolved settings, so `config.yaml` stays the single source of truth —
the client decides nothing, it just stops being a relay.

Providers that can only run on the gateway host (local whisper, command providers)
resolve to `{"mode": "relay"}` and the old path is used unchanged. So does a
dashboard that is down. The client also falls back for the rest of the session if
a direct upload fails, so a revoked key degrades instead of breaking the
conversation.

**This route hands out a provider credential**, which makes it exactly as safe as
the gate in front of it — the reason that gate fails closed. The key is held in a
JavaScript variable for the session and never written to `localStorage` or a URL.
With `?debug=1` the timing strip shows `stt-diretto` or `stt-relay`, so which path
ran is never a guess.

## Access control

The tunnel URL is public, and the agent behind it can search the web, read
memory and spend API credits — an open URL is an open agent. Every route except
`/health` and `/api/health` requires a token.

```bash
echo "VOICE_AUTH_TOKEN=$(openssl rand -hex 32)" >> .env
```

Open the app once as `https://your-url/?k=<token>`, or just open it and paste the
token into the login page — a browser hitting a gated route gets a form back, not
raw JSON, so a locked-out phone has a way in. API calls still get a bare 401.

**On iOS, add the app to the home screen from the `?k=` URL.** A standalone web
app has its own cookie jar, so authenticating in Safari does not necessarily
authenticate the home-screen icon.

The server replies with an
HttpOnly, signed cookie valid for a year, so the token does not have to live in
the home-screen URL — and the cookie carries only an expiry plus its HMAC, never
the token itself. Same-origin fetches send it automatically, so the PWA needs no
change.

**It fails closed.** With `VOICE_AUTH_TOKEN` unset the server refuses every
request rather than serving an open agent: a control that silently allows
everything when misconfigured is worse than none, because it looks protected.

**Requests that change something must come from the app's own page.** A signed-in
phone that opens some other website must not be usable to post here on its
behalf, so POSTs carrying another site's `Origin` or `Sec-Fetch-Site: cross-site`
are refused. There is no CORS: the only client is the app itself.

The hub moved from Flask to FastAPI with the same cookie name and signature, so a
phone signed in to the old server stays signed in.

This is a single shared secret, appropriate for one person's assistant. It is
not user accounts, and it does not rotate on its own.

## Security Notes

- **Never commit `.env`** — it's in `.gitignore`
- **Keep `~/.hermes/.env` private** — it contains your OpenRouter API key and Discord bot token
- The hub binds to `127.0.0.1:5000` only: the tunnel reaches it locally, nothing else can
- The hub runs as the unprivileged `hermes-hub` user, with a read-only view of the system
  and no access to `/root` (see the hardening block in `deploy/hermes-hub.service`)
- Access logging is off, because the one-time `?k=<token>` login would otherwise be
  written to the journal in clear
- The Hermes Agent API (port 8642) is local-only by default — do not expose it publicly
- The ngrok URL is public: the token gate above is what stands between it and the agent

---

## License

MIT
