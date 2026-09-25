"""Hermes Hub: one app on the phone for talking to Hermes and running the server.

    uvicorn hub.main:app --app-dir server --host 127.0.0.1 --port 5000

Phase 0 serves the voice pipeline plus the new app shell. The routes are the
old voice routes moved under /api.
"""
import base64
import json

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import audit, config, discord_mirror, hermes, security
from .speech import clean_for_tts, sse, take_sentence

app = FastAPI(title="Hermes Hub", docs_url=None, redoc_url=None, openapi_url=None)

for line in config.warnings():
    print("WARNING:", line)
print("Discord mirroring:", "enabled" if discord_mirror.enabled() else "disabled")


def error(message, status):
    return JSONResponse({"error": message}, status_code=status)


@app.middleware("http")
async def gate(request: Request, call_next):
    denied = security.check(request)
    if denied:
        status, message = denied
        audit.record("anon", "denied", f"{status} {request.method} {request.url.path}", ok=False)
        # A browser opening the page gets a way back in; anything else gets a
        # bare error. Without this a locked-out phone just renders raw JSON.
        if status == 401 and "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(security.LOGIN_PAGE, status_code=401)
        return error(message, status)
    response = await call_next(request)
    security.grant_cookie(request, response)
    return response


# ── API ───────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/api/health")
async def health():
    return {"status": "ok"}


# The dashboard rejects uploads above this; checking first gives a clear
# message instead of an opaque 413 from further down.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


@app.get("/api/voice-config")
async def voice_config():
    """The STT settings the client may use to reach the provider itself.

    Relaying audio through the server cost ~2s more than the transcription
    itself, measured from a phone. When the configured provider can be reached
    from a browser, the client goes straight there and only the transcript
    comes back. This hands out a provider credential, so it is only ever as safe
    as the gate in front of it — the reason the gate fails closed.
    """
    try:
        cfg = await hermes.dashboard_get("/api/audio/voice-config")
    except hermes.HermesError as e:
        # Never fail the session over this: the relay path still works.
        print(f"[VOICE-CONFIG] {e}")
        return {"stt": {"mode": "relay", "reason": str(e)}}
    stt = (cfg or {}).get("stt") or {"mode": "relay", "reason": "no stt block"}
    print(f"[VOICE-CONFIG] stt mode={stt.get('mode')} provider={stt.get('provider')}")
    return {"stt": stt}


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(None)):
    if audio is None:
        return error("no audio file", 400)
    mime = (audio.content_type or "audio/webm").split(";")[0].strip()
    data = await audio.read()
    if not data:
        return error("empty audio", 400)
    if len(data) > MAX_AUDIO_BYTES:
        return error("audio too large", 413)
    data_url = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    try:
        result = await hermes.dashboard_post(
            "/api/audio/transcribe", {"data_url": data_url, "mime_type": mime})
    except hermes.HermesError as e:
        print(f"[STT ERROR] {e}")
        return error(str(e), e.status)
    # An empty transcript is a normal outcome: the dashboard maps "no speech"
    # to a successful empty result, and the client treats short text as silence.
    text = (result.get("transcript") or "").strip()
    print(f"[STT] {text!r} (provider={result.get('provider')})")
    return {"text": text}


@app.post("/api/tts")
async def tts(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return error("text required", 400)
    try:
        result = await hermes.dashboard_post("/api/audio/speak", {"text": text})
    except hermes.HermesError as e:
        print(f"[TTS ERROR] {e}")
        return error(str(e), e.status)
    # The dashboard returns a data URL; the client wants bare base64 plus the
    # mime, because the configured provider decides the format.
    data_url = result.get("data_url") or ""
    if "," not in data_url:
        return error("dashboard returned no audio", 502)
    return {"audio": data_url.split(",", 1)[1], "mime": result.get("mime_type")}


VOICE_SYSTEM_PROMPT = (
    "Sei Hermes, un assistente vocale personale. "
    "Rispondi SEMPRE e SOLO in italiano, qualunque cosa scriva l'utente. "
    "Le tue risposte vengono lette ad alta voce da un sintetizzatore vocale: "
    "usa frasi brevi e naturali, come in una conversazione parlata. "
    "Non usare mai markdown, asterischi, elenchi puntati, simboli speciali o codice. "
    "Sii conciso: massimo 2-3 frasi per risposta, salvo quando l'utente chiede esplicitamente dettagli."
)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    history = body.get("history") or []
    session_id = body.get("session_id")
    user_text = history[-1]["content"] if history else ""
    audit.record("pwa", "chat", f"session={session_id} chars={len(user_text)}")

    messages = [{"role": "system", "content": VOICE_SYSTEM_PROMPT}] + history
    try:
        c, upstream = await hermes.open_chat_stream(messages, session_id)
    except hermes.HermesError as e:
        print(f"[CHAT ERROR] {e}")
        return error(str(e), e.status)

    async def generate():
        buf, full, event, n_sent = "", "", "", 0
        try:
            async for line in upstream.aiter_lines():
                line = line.strip()
                if not line:
                    event = ""          # a blank line closes an SSE event
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
                buf += delta
                while True:
                    sentence, buf = take_sentence(buf, first=(n_sent == 0))
                    if not sentence:
                        break
                    spoken = clean_for_tts(sentence)
                    if spoken:
                        n_sent += 1
                        yield sse({"sentence": spoken})

            # Whatever never reached a sentence boundary is said anyway, or a
            # reply that ends without punctuation is silently dropped.
            tail = clean_for_tts(buf)
            if tail:
                yield sse({"sentence": tail})
            print(f"[HERMES] {full[:80]!r}")
            discord_mirror.mirror(user_text, full, session_id)
            yield sse({"done": True, "reply": clean_for_tts(full)})
        except Exception as e:
            print(f"[CHAT STREAM ERROR] {e}")
            yield sse({"error": str(e)})
        finally:
            await upstream.aclose()
            await c.aclose()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        # Reverse proxies buffer by default, which would hold every sentence
        # back until the stream ends and undo the whole point.
        "X-Accel-Buffering": "no",
    })


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def api_not_found(rest: str):
    # Without this the SPA fallback below would answer unknown API calls with
    # index.html and a 200, which reads as success to a fetch.
    return error("not found", 404)


# ── App shell ─────────────────────────────────────────────────────
# Hashed assets never change under the same name, so they can be cached for
# good. index.html must never be cached: Safari holds a home-screen app's shell
# aggressively, and a stale one looks exactly like a change that did not work.
@app.get("/{path:path}")
async def shell(path: str):
    dist = config.WEB_DIST.resolve()
    if path:
        target = (dist / path).resolve()
        if target.is_file() and dist in target.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
                if path.startswith("assets/") else {"Cache-Control": "no-cache"}
            return FileResponse(target, headers=headers)
    index = dist / "index.html"
    if not index.is_file():
        return error("frontend not built: run `npm run build` in web/", 503)
    return FileResponse(index, headers={"Cache-Control": "no-store, must-revalidate"})
