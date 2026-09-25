"""Hermes Hub: one app on the phone for talking to Hermes and running the server.

    uvicorn hub.main:app --app-dir server --host 127.0.0.1 --port 5000

Phase 1: one conversation for voice and text on Hermes' own sessions, every
channel's transcripts, and approvals answered from the phone.
"""
import asyncio
import base64
import re
import time

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import audit, config, hermes, runs, security, transcript
from .speech import sse

app = FastAPI(title="Hermes Hub", docs_url=None, redoc_url=None, openapi_url=None)

for line in config.warnings():
    print("WARNING:", line)


def error(message, status):
    return JSONResponse({"error": message}, status_code=status)


@app.exception_handler(hermes.HermesError)
async def hermes_error(request: Request, exc: hermes.HermesError):
    print(f"[HERMES ERROR] {request.method} {request.url.path}: {exc}")
    return error(str(exc), exc.status)


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

# Hermes session ids seen so far: uuids, api_<ts>_<hex>, dated gateway ids.
# Anything else is refused before it can reach a URL.
SESSION_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")
RUN_ID = re.compile(r"^run_[0-9a-f]{32}$")
MAX_MESSAGE_CHARS = 20000


def _session_id(value):
    if not isinstance(value, str) or not SESSION_ID.match(value):
        raise hermes.HermesError("invalid session id", status=400)
    return value


def _run_id(value):
    if not RUN_ID.match(value):
        raise hermes.HermesError("invalid run id", status=400)
    return value


@app.get("/api/sessions")
async def sessions(limit: int = 30, offset: int = 0):
    data = await hermes.api_request("GET", "/api/sessions", params={
        "limit": min(max(limit, 1), 100), "offset": max(offset, 0)})
    return {"sessions": [transcript.session(s) for s in data.get("data") or []],
            "has_more": bool(data.get("has_more"))}


@app.get("/api/sessions/search")
async def search(q: str = ""):
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    data = await hermes.dashboard_get("/api/sessions/search", params={"q": q[:200]})
    # One hit per conversation: the list is for finding it, the thread shows the rest.
    hits, seen = [], set()
    for raw in data.get("results") or []:
        hit = transcript.search_hit(raw)
        if hit["session_id"] and hit["session_id"] not in seen:
            seen.add(hit["session_id"])
            hits.append(hit)
    return {"results": hits[:40]}


@app.get("/api/sessions/{session_id}")
async def session_thread(session_id: str):
    sid = hermes.seg(_session_id(session_id))
    meta, messages = await asyncio.gather(
        hermes.api_request("GET", f"/api/sessions/{sid}"),
        hermes.api_request("GET", f"/api/sessions/{sid}/messages", params={"limit": 300}))
    return {"session": transcript.session(meta.get("session") or {}),
            "items": transcript.items(messages.get("data") or [])}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return error("message required", 400)
    if len(message) > MAX_MESSAGE_CHARS:
        return error("message too long", 413)
    voice = bool(body.get("voice"))
    if body.get("session_id"):
        session_id = _session_id(body["session_id"])
    else:
        # Created here rather than by the phone: one round trip less on the
        # first turn, which is the one that feels slow.
        created = await hermes.api_request("POST", "/api/sessions", payload={})
        session_id = created["session"]["id"]
    audit.record("pwa", "chat", f"session={session_id} chars={len(message)} voice={voice}")
    run = await runs.start(session_id, message, voice, VOICE_SYSTEM_PROMPT if voice else None)
    return event_stream(run)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, after: int = -1):
    run = runs.get(_run_id(run_id))
    if not run:
        # Finished long ago or the hub restarted: the transcript has the result.
        return error("run not found", 404)
    return event_stream(run, after)


# "always" is left out on purpose: a permanent approval changes Hermes'
# config for every channel, which is not a decision for a phone notification.
APPROVAL_CHOICES = {"once", "session", "deny"}


@app.post("/api/runs/{run_id}/approval")
async def approval(run_id: str, request: Request):
    run_id = _run_id(run_id)
    body = await request.json()
    choice, request_id = body.get("choice"), body.get("request_id")
    if choice not in APPROVAL_CHOICES:
        return error("choice must be once, session or deny", 400)
    payload = {"choice": choice}
    if request_id is not None:
        if not isinstance(request_id, str) or not 0 < len(request_id) <= 256:
            return error("invalid request_id", 400)
        payload["request_id"] = request_id
    run = runs.get(run_id)
    command = next((e["command"] for e in reversed(run.events) if e["type"] == "approval"
                    and e.get("request_id") == request_id), "") if run else ""
    detail = f"run={run_id} choice={choice} command={command[:160]!r}"
    try:
        await hermes.api_request("POST", f"/v1/runs/{hermes.seg(run_id)}/approval", payload=payload)
    except hermes.HermesError:
        audit.record("pwa", "approval", detail, ok=False)
        raise
    audit.record("pwa", "approval", detail)
    if run:
        run.push("approval_done", request_id=request_id, choice=choice)
    return {"ok": True, "choice": choice}


@app.post("/api/runs/{run_id}/stop")
async def stop(run_id: str):
    run_id = _run_id(run_id)
    result = await hermes.api_request("POST", f"/v1/runs/{hermes.seg(run_id)}/stop", payload={})
    audit.record("pwa", "stop", f"run={run_id}")
    return {"ok": True, "status": result.get("status")}


# Text arrives a few characters at a time. Padding every piece past ngrok's
# buffer would cost 2KB per token; padding only what must be seen now (a
# sentence to speak, a tool, an approval, the end), and otherwise at most every
# quarter second, keeps the thread live for a fraction of the bytes.
FLUSH_EVERY_S = 0.25
KEEPALIVE_S = 15


def _merge_deltas(batch):
    out = []
    for ev in batch:
        if ev["type"] == "delta" and out and out[-1]["type"] == "delta":
            out[-1] = {**ev, "text": out[-1]["text"] + ev["text"]}
        else:
            out.append(ev)
    return out


def event_stream(run, after=-1):
    async def generate():
        last_pad = last_write = time.monotonic()
        unflushed = False
        async for batch in run.follow(after, keepalive=FLUSH_EVERY_S):
            now = time.monotonic()
            if not batch:
                if unflushed:
                    yield sse({"type": "flush"})
                    unflushed, last_pad, last_write = False, now, now
                elif now - last_write >= KEEPALIVE_S:
                    yield ": keepalive\n\n"
                    last_write = now
                continue
            events = _merge_deltas(batch)
            pad = any(e["type"] != "delta" for e in events) or now - last_pad >= FLUSH_EVERY_S
            yield sse(events[0], pad=pad) + "".join(sse(e, pad=False) for e in events[1:])
            last_write = now
            if pad:
                last_pad = now
            unflushed = not pad

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
