import base64, os, json, re, threading, time, hmac, hashlib, urllib.request, urllib.error
from flask import Flask, request, jsonify, send_from_directory, Response, abort
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

# Access control. This server sits behind a public tunnel URL, and the agent it
# fronts can search the web, read memory and spend API credits — an open URL is
# an open agent. Fails closed on purpose: an auth control that silently allows
# everything when misconfigured is worse than none, because it looks protected.
VOICE_AUTH_TOKEN = os.environ.get("VOICE_AUTH_TOKEN", "")
_AUTH_COOKIE     = "hv_auth"
_AUTH_MAX_AGE    = 365 * 24 * 3600      # a phone should not re-authenticate often
_PUBLIC_PATHS    = {"/health"}


def _auth_cookie_value(expires_at):
    """expiry + HMAC over it. The token itself never travels in the cookie."""
    raw = str(int(expires_at))
    sig = hmac.new(VOICE_AUTH_TOKEN.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw + "." + sig


def _auth_cookie_ok(value):
    try:
        raw, sig = (value or "").split(".", 1)
        if int(raw) < time.time():
            return False
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(VOICE_AUTH_TOKEN.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# Discord mirroring — optional. Set both vars to enable.
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN",   "")
DISCORD_UA      = "DiscordBot (https://github.com/lorenzotelesco-png/hermes-voice, 1.0)"
DISCORD_ENABLED = bool(DISCORD_WEBHOOK and DISCORD_TOKEN)

if not VOICE_AUTH_TOKEN:
    print("WARNING: VOICE_AUTH_TOKEN is not set — every request will be refused with 503.")
    print("         Generate one with: openssl rand -hex 32")
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


# A sentence ends at .!? only when what follows is not a lowercase letter, so
# "Dr. Rossi" and "es. questo" stay whole. Same rule the client used to apply,
# moved here so the browser no longer needs to know about it.
_SENTENCE_END = re.compile(r'[.!?](?=\s+[^a-z\s]|\s*$)')

# Below this, a "sentence" is a fragment ("Ok.") and synthesizing it on its own
# costs a round trip for a word. Hermes' own speaker pipeline uses the same floor.
_MIN_SENTENCE_CHARS = 20


# The FIRST fragment is allowed to be much shorter. A reply that opens with
# "Certo!" (6 chars) would otherwise be held back until the next sentence
# completed — measured on a phone, that is the difference between hearing
# something at 5s and hearing nothing until 8.5s. Paying a synthesis round trip
# for one word is worth it exactly once, at the start, where all the silence is.
_MIN_FIRST_CHARS = 4


def take_sentence(buf, first=False):
    """Split off the first complete sentence. Returns (sentence|None, remainder)."""
    floor = _MIN_FIRST_CHARS if first else _MIN_SENTENCE_CHARS
    for m in _SENTENCE_END.finditer(buf):
        end = m.end()
        if end >= floor:
            return buf[:end].strip(), buf[end:].lstrip()
    return None, buf


# ngrok's free tier will not reliably flush a small chunk: the same payload
# streams correctly one run and arrives as one 42ms burst the next, which is
# heard as "it generated everything, then started talking". Proxies flush once a
# chunk crosses their buffer threshold, so every event is padded past it with an
# SSE comment (a line starting with ":" is ignored by every SSE parser).
# Wasteful, but 2KB against a sentence of speech is not a real cost.
_FLUSH_PAD = ": " + (" " * 2048) + "\n"


def sse(payload):
    return _FLUSH_PAD + "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


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
@app.before_request
def require_auth():
    """Gate everything but /health.

    Entry is ?k=<token> once; after that a signed cookie carries the session, so
    the token does not have to live in the home-screen URL. Same-origin fetches
    send the cookie on their own, so the client needs no changes.
    """
    if not VOICE_AUTH_TOKEN:
        # Fail closed. Serving an open agent because a variable is unset is the
        # failure mode this control exists to prevent.
        return jsonify({"error": "VOICE_AUTH_TOKEN is not set on the server"}), 503
    if request.path in _PUBLIC_PATHS:
        return None
    key = request.args.get("k", "")
    if key and hmac.compare_digest(key, VOICE_AUTH_TOKEN):
        request.environ["hv_grant_cookie"] = True
        return None
    if _auth_cookie_ok(request.cookies.get(_AUTH_COOKIE)):
        return None
    # Deliberately terse: an unauthenticated caller learns nothing about what
    # runs here.
    return jsonify({"error": "unauthorized"}), 401


@app.after_request
def grant_auth_cookie(resp):
    if request.environ.pop("hv_grant_cookie", False):
        resp.set_cookie(
            _AUTH_COOKIE, _auth_cookie_value(time.time() + _AUTH_MAX_AGE),
            max_age=_AUTH_MAX_AGE, httponly=True, samesite="Lax",
            # The tunnel terminates TLS, so the cookie must never travel plain.
            secure=request.headers.get("X-Forwarded-Proto", "https") == "https",
        )
    return resp


@app.after_request
def no_cache_pwa(resp):
    """Never let the PWA shell be cached.

    Safari caches aggressively for a page added to the home screen, so a
    deployed app.js can keep running the previous version with no visible sign
    that anything is stale — the change looks like it simply did not work.
    These files are a few KB; re-fetching them costs nothing next to the
    confusion of debugging a version that is not the one on disk.
    """
    if request.path in ("/", "/index.html", "/app.js"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


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

        # Streamed, so the first sentence can be spoken while the model is still
        # writing the rest. Waiting for the complete reply before synthesizing was
        # the single largest source of dead air in this pipeline.
        payload = json.dumps({
            "model": HERMES_MODEL,
            "messages": messages,
            "max_tokens": HERMES_MAX_TOKENS,
            "stream": True,
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
        upstream = urllib.request.urlopen(req, timeout=60)
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

    def generate():
        buf, full, event = "", "", ""
        n_sent = 0
        try:
            for raw in upstream:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    event = ""          # blank line closes an SSE event
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                # Hermes emits event: hermes.tool.progress for tool-start UX.
                # It is not reply text and must never reach the speaker.
                if event and event != "message":
                    continue
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content")
                if not delta:
                    continue
                full += delta
                buf  += delta
                while True:
                    sentence, buf = take_sentence(buf, first=(n_sent == 0))
                    if not sentence:
                        break
                    spoken = clean_for_tts(sentence)
                    if spoken:
                        n_sent += 1
                        yield sse({"sentence": spoken})

            # Whatever is left never reached a sentence boundary — say it anyway,
            # otherwise a reply that ends without punctuation is silently dropped.
            tail = clean_for_tts(buf)
            if tail:
                yield sse({"sentence": tail})

            print(f"[HERMES] {repr(full[:80])}")
            state = _discord_states.setdefault(session_id or "default", {})
            threading.Thread(target=mirror_to_discord,
                             args=(user_text, full, state), daemon=True).start()
            yield sse({"done": True, "reply": clean_for_tts(full)})
        except Exception as e:
            print(f"[CHAT STREAM ERROR] {e}")
            yield sse({"error": str(e)})
        finally:
            upstream.close()

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        # ngrok and most reverse proxies buffer responses by default, which would
        # hold every sentence back until the stream ends and undo the whole point.
        "X-Accel-Buffering": "no",
    })


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
