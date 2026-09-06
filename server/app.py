import base64, os, json, re, threading, urllib.request, urllib.error
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Load .env file if present (requires python-dotenv, optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="../web", static_url_path="")
CORS(app)

# ── Config (all values from environment variables) ────────────────
HERMES_API   = os.environ.get("HERMES_API_URL",      "http://127.0.0.1:8642/v1/chat/completions")

# Speech I/O runs on the Hermes dashboard, not here. STT and TTS providers,
# models and language all come from ~/.hermes/config.yaml (stt.*, tts.*) — this
# server only forwards audio. That is why there is no Whisper model to load and
# no Piper binary to shell out to any more.
DASHBOARD_URL   = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119")
# Must equal HERMES_DASHBOARD_SESSION_TOKEN in the dashboard's environment.
# Left unset, the dashboard mints a random token per start and every call 401s.
DASHBOARD_TOKEN = os.environ.get("HERMES_DASHBOARD_TOKEN", "")

# Hermes Agent API auth. Since 2026 the API server requires a bearer token on
# EVERY deployment, including the default loopback bind on 127.0.0.1 — requests
# without it are rejected with 401. Must match API_SERVER_KEY in ~/.hermes/.env.
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "")
# Advertised model name on /v1/models. Defaults to the profile name, or
# "hermes-agent" for the default profile. Override with API_SERVER_MODEL_NAME.
HERMES_MODEL   = os.environ.get("HERMES_MODEL", "hermes-agent")
HERMES_MAX_TOKENS = int(os.environ.get("HERMES_MAX_TOKENS", "800"))

# Discord mirroring — optional. Set both vars to enable.
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN",   "")
DISCORD_UA      = "DiscordBot (https://github.com/lorenzotelesco-png/hermes-voice, 1.0)"
DISCORD_ENABLED = bool(DISCORD_WEBHOOK and DISCORD_TOKEN)

if not DASHBOARD_TOKEN:
    print("WARNING: HERMES_DASHBOARD_TOKEN is not set — /transcribe and /tts will 401.")
    print("         Set HERMES_DASHBOARD_SESSION_TOKEN on the dashboard service to a fixed")
    print("         value and mirror it here, otherwise the token is random per restart.")
if not HERMES_API_KEY:
    print("WARNING: HERMES_API_KEY is not set — Hermes Agent will reject /chat with 401.")
    print("         Set API_SERVER_KEY in ~/.hermes/.env and mirror it here as HERMES_API_KEY.")
if DISCORD_ENABLED:
    print("Discord mirroring: enabled")
else:
    print("Discord mirroring: disabled (set DISCORD_WEBHOOK_URL + DISCORD_BOT_TOKEN to enable)")

# ── Helpers ───────────────────────────────────────────────────────
class DashboardError(Exception):
    """A call to the Hermes dashboard audio API failed."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def dashboard_post(path, payload, timeout=60):
    """POST JSON to the Hermes dashboard and return the decoded response.

    Auth uses the dedicated session header rather than Authorization: the
    dashboard prefers it precisely because Authorization collides with reverse
    proxies that do their own basic auth.
    """
    req = urllib.request.Request(
        f"{DASHBOARD_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Hermes-Session-Token": DASHBOARD_TOKEN,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code == 401:
            raise DashboardError(
                "Dashboard rejected the session token. HERMES_DASHBOARD_TOKEN must match "
                "HERMES_DASHBOARD_SESSION_TOKEN on the dashboard service.")
        raise DashboardError(f"Dashboard HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise DashboardError(
            f"Dashboard unreachable at {DASHBOARD_URL} ({e.reason}). "
            f"Is `hermes dashboard` running?", status=503)


def clean_for_tts(text):
    text = re.sub(r'<@!?\d+>', '', text)
    text = re.sub(r'<#\d+>', '', text)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
    text = re.sub(r'#+\s', '', text)
    text = re.sub(r'\n{2,}', ' ', text)
    return text.strip()

def _discord_post(payload_dict, url):
    try:
        data = json.dumps(payload_dict).encode()
        req  = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_UA,
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[DISCORD] {e}")
        return None

def _discord_bot(path, payload_dict):
    try:
        data = json.dumps(payload_dict).encode()
        req  = urllib.request.Request(
            f"https://discord.com/api/v10{path}", data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {DISCORD_TOKEN}",
                "User-Agent": DISCORD_UA,
            }
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[DISCORD BOT] {e}")
        return None

def mirror_to_discord(user_text, hermes_reply, discord_state):
    """Post user message + Hermes reply to a Discord thread (fire-and-forget)."""
    if not DISCORD_ENABLED:
        return
    try:
        if not discord_state.get("thread_id"):
            msg = _discord_post(
                {"content": "🎙️ **Sessione vocale**", "username": "Hermes Voice"},
                DISCORD_WEBHOOK + "?wait=true"
            )
            if not msg:
                return
            thread = _discord_bot(
                f"/channels/{msg['channel_id']}/messages/{msg['id']}/threads",
                {"name": "Conversazione vocale", "auto_archive_duration": 60}
            )
            if not thread:
                return
            discord_state["thread_id"] = thread["id"]

        tid        = discord_state["thread_id"]
        thread_url = DISCORD_WEBHOOK + f"?wait=true&thread_id={tid}"
        _discord_post({"content": f"🎤 {user_text}", "username": "Lorenzo"}, thread_url)
        _discord_post({"content": hermes_reply,       "username": "Hermes"},  thread_url)

    except Exception as e:
        print(f"[MIRROR] {e}")

# Per-session Discord thread state (keyed by session_id)
_discord_states = {}

# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("../web", "index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# The dashboard rejects uploads above this; we check first so an oversized clip
# fails here with a clear message instead of as an opaque 413 from the proxy.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "no audio file"}), 400
    f     = request.files["audio"]
    mime  = (f.content_type or "audio/webm").split(";")[0].strip()
    audio = f.read()
    if not audio:
        return jsonify({"error": "empty audio"}), 400
    if len(audio) > MAX_AUDIO_BYTES:
        return jsonify({"error": "audio too large"}), 413

    data_url = f"data:{mime};base64," + base64.b64encode(audio).decode("ascii")
    try:
        result = dashboard_post(
            "/api/audio/transcribe", {"data_url": data_url, "mime_type": mime})
    except DashboardError as e:
        print(f"[STT ERROR] {e}")
        return jsonify({"error": str(e)}), e.status

    # An empty transcript is a normal outcome, not an error: the dashboard maps
    # "no speech detected" to a successful empty result so a VAD loop can just
    # re-listen. The client already treats short text as silence.
    text = (result.get("transcript") or "").strip()
    print(f"[STT] {repr(text)} (provider={result.get('provider')})")
    return jsonify({"text": text})

@app.route("/chat", methods=["POST"])
def chat():
    data       = request.get_json(force=True)
    history    = data.get("history", [])
    session_id = data.get("session_id")   # local ID for Discord thread tracking only
    user_text  = history[-1]["content"] if history else ""

    try:
        # Prepend system prompt — enforces Italian and voice-friendly style.
        # Done here so it applies to every request without touching Hermes config.
        system_prompt = {
            "role": "system",
            "content": (
                "Sei Hermes, un assistente vocale personale. "
                "Rispondi SEMPRE e SOLO in italiano, qualunque cosa scriva l'utente. "
                "Le tue risposte vengono lette ad alta voce da un sintetizzatore vocale: "
                "usa frasi brevi e naturali, come in una conversazione parlata. "
                "Non usare mai markdown, asterischi, elenchi puntati, simboli speciali o codice. "
                "Sii conciso: massimo 2-3 frasi per risposta, salvo quando l'utente chiede esplicitamente dettagli."
            ),
        }
        messages = [system_prompt] + history

        # Send messages to Hermes Gateway (stateless OpenAI-compatible mode).
        # Context is carried by the messages array; Hermes handles fallback to
        # Ollama locally when the cloud model is rate-limited or unavailable.
        payload = json.dumps({
            "model": HERMES_MODEL,
            "messages": messages,
            "max_tokens": HERMES_MAX_TOKENS,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if HERMES_API_KEY:
            headers["Authorization"] = f"Bearer {HERMES_API_KEY}"
        if session_id:
            # Transcript scope: keeps this voice session as one conversation in
            # the dashboard and session history instead of N orphaned turns.
            headers["X-Hermes-Session-Id"] = session_id
            # Stable long-term memory scope — deliberately NOT the session id,
            # which rotates per voice session.
            headers["X-Hermes-Session-Key"] = "hermes-voice:pwa"
        req = urllib.request.Request(HERMES_API, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())

        reply_raw = result["choices"][0]["message"]["content"].strip()
        reply     = clean_for_tts(reply_raw)
        print(f"[HERMES] {repr(reply[:80])}")

        # Keep the same session_id for Discord thread continuity
        state = _discord_states.setdefault(session_id or "default", {})
        threading.Thread(
            target=mirror_to_discord,
            args=(user_text, reply_raw, state),
            daemon=True
        ).start()

        return jsonify({"reply": reply, "session_id": session_id})

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        print(f"[CHAT ERROR] HTTP {e.code}: {body}")
        if e.code in (401, 403):
            return jsonify({"error": f"Hermes rejected the request ({e.code}). "
                                     f"Check HERMES_API_KEY matches API_SERVER_KEY."}), 502
        return jsonify({"error": f"Hermes HTTP {e.code}: {body}"}), 502
    except Exception as e:
        print(f"[CHAT ERROR] {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/tts", methods=["POST"])
def tts():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    try:
        result = dashboard_post("/api/audio/speak", {"text": text})
    except DashboardError as e:
        print(f"[TTS ERROR] {e}")
        return jsonify({"error": str(e)}), e.status

    # The dashboard returns a data URL; the client wants bare base64 in "audio".
    # Keeping that shape means the PWA needs no change. mime is sent alongside
    # because the configured provider decides the format (Piper WAV, others MP3)
    # — decodeAudioData handles both, but the client should not have to guess.
    data_url = result.get("data_url") or ""
    if "," not in data_url:
        return jsonify({"error": "dashboard returned no audio"}), 502
    return jsonify({
        "audio": data_url.split(",", 1)[1],
        "mime": result.get("mime_type"),
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
