import subprocess, base64, tempfile, os, json, re, threading, urllib.request, urllib.error
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from faster_whisper import WhisperModel

# Load .env file if present (requires python-dotenv, optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="../web", static_url_path="")
CORS(app)

# ── Config (all values from environment variables) ────────────────
MODEL_PATH   = os.environ.get("PIPER_MODEL_PATH",   "models/it_IT/it_IT-paola-medium.onnx")
MODEL_CONFIG = os.environ.get("PIPER_MODEL_CONFIG",  "models/it_IT/it_IT-paola-medium.onnx.json")
HERMES_API   = os.environ.get("HERMES_API_URL",      "http://127.0.0.1:8642/v1/chat/completions")
STT_LANGUAGE = os.environ.get("STT_LANGUAGE",        "it")

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

MIME_EXT = {
    "audio/webm": ".webm", "audio/webm;codecs=opus": ".webm",
    "audio/ogg": ".ogg",   "audio/ogg;codecs=opus": ".ogg",
    "audio/mp4": ".m4a",   "audio/mpeg": ".mp3",
    "audio/wav": ".wav",   "audio/x-wav": ".wav",
}

print("Loading Whisper model...")
whisper = WhisperModel("small", device="cpu", compute_type="int8")
print("Whisper ready.")
if not HERMES_API_KEY:
    print("WARNING: HERMES_API_KEY is not set — Hermes Agent will reject /chat with 401.")
    print("         Set API_SERVER_KEY in ~/.hermes/.env and mirror it here as HERMES_API_KEY.")
if DISCORD_ENABLED:
    print("Discord mirroring: enabled")
else:
    print("Discord mirroring: disabled (set DISCORD_WEBHOOK_URL + DISCORD_BOT_TOKEN to enable)")

# ── Helpers ───────────────────────────────────────────────────────
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

@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "no audio file"}), 400
    f    = request.files["audio"]
    mime = f.content_type or "audio/webm"
    ext  = MIME_EXT.get(mime.split(";")[0].strip(), ".webm")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        f.save(tmp.name); path = tmp.name
    try:
        segs, _ = whisper.transcribe(path, language=STT_LANGUAGE, beam_size=1, vad_filter=False)
        text = " ".join(s.text for s in segs).strip()
        print(f"[STT] {repr(text)}")
        return jsonify({"text": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(path)

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
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    try:
        proc = subprocess.run(
            ["piper", "-m", MODEL_PATH, "-c", MODEL_CONFIG, "-f", out],
            input=text.encode(), capture_output=True, timeout=30
        )
        if proc.returncode != 0:
            return jsonify({"error": proc.stderr.decode()}), 500
        with open(out, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()
        return jsonify({"audio": audio_b64})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(out)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
