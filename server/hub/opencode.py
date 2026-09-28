"""The Code tab: OpenCode's sessions and terminals, behind Face ID.

OpenCode runs as a server on the VPS (`opencode serve`, loopback only, with a
password; the hub holds it and the phone never sees it). Everything here is a
thin translation of its API into what a phone draws:

- sessions in every folder, a transcript of text, reasoning and tool cards,
  a prompt that returns at once while OpenCode works on (its turns do not
  depend on this connection, so iOS dropping it loses nothing), stop, and its
  permission requests;
- live events, from OpenCode's global stream, cut down to one session;
- terminals: OpenCode's PTYs, a shell or OpenCode's own interface, which keep
  running when the phone goes away and replay what was missed on return.

All of it needs the Face ID unlock (passkey.py), checked on every request and
on the terminal's WebSocket, which the HTTP gate does not see.
"""
import asyncio
import base64
import json
import os
import re
import time

import httpx
from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from . import audit, config, passkey, security
from .speech import sse

transport = None            # tests swap in an httpx.MockTransport
ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
KEEPALIVE_S = 15
MAX_PROMPT = 20000
TOOL_OUTPUT = 3000


class OpenCodeError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def require_unlocked(request: Request):
    if not passkey.unlocked_until(request):
        raise OpenCodeError("sblocca con Face ID", status=401)


router = APIRouter(dependencies=[Depends(require_unlocked)])
ws_router = APIRouter()


def client(timeout=15):
    return httpx.AsyncClient(base_url=config.OPENCODE_URL, transport=transport, timeout=timeout,
                             auth=("opencode", config.OPENCODE_PASSWORD))


async def oc(method, path, params=None, payload=None, timeout=15):
    try:
        async with client(timeout) as c:
            r = await c.request(method, path, params=params, json=payload)
    except httpx.HTTPError as e:
        raise OpenCodeError(f"OpenCode non risponde: {e.__class__.__name__}", 503) from e
    if r.status_code == 401:
        raise OpenCodeError("password di OpenCode sbagliata (HUB_OPENCODE_PASSWORD)", 502)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message") or r.json().get("error") or r.text
        except ValueError:
            msg = r.text
        raise OpenCodeError(f"OpenCode: {str(msg)[:200]}", 404 if r.status_code == 404 else 502)
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def _id(value):
    if not isinstance(value, str) or not ID.match(value):
        raise OpenCodeError("id non valido", 400)
    return value


def _dir(value):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value or len(value) > 400 \
            or "/../" in value + "/":
        raise OpenCodeError("cartella non valida: serve un percorso assoluto", 400)
    return value.rstrip("/") or "/"


def _items(data):
    return data if isinstance(data, list) else (data or {}).get("items") or (data or {}).get("data") or []


# ── what the phone draws ──────────────────────────────────────────
def session_row(s):
    t = s.get("time") or {}
    summary = s.get("summary") or {}
    return {"id": s.get("id"), "title": s.get("title") or "", "dir": s.get("directory") or "",
            "created": (t.get("created") or 0) / 1000, "updated": (t.get("updated") or 0) / 1000,
            "changes": {k: summary.get(k, 0) for k in ("additions", "deletions", "files")}}


def _tool_input(state):
    i = state.get("input") or {}
    for k in ("command", "filePath", "path", "pattern", "url", "description", "query"):
        if i.get(k):
            return str(i[k])[:500]
    return json.dumps(i, ensure_ascii=False)[:300] if i else ""


def part_view(p):
    t = p.get("type")
    base = {"id": p.get("id"), "type": t}
    if t in ("text", "reasoning"):
        return {**base, "text": p.get("text") or ""} if (p.get("text") or "").strip() else None
    if t == "tool":
        s = p.get("state") or {}
        out = s.get("output") if isinstance(s.get("output"), str) else ""
        return {**base, "tool": p.get("tool") or "", "status": s.get("status") or "",
                "title": s.get("title") or "", "input": _tool_input(s),
                "output": out[-TOOL_OUTPUT:], "cut": len(out) > TOOL_OUTPUT, "error": str(s.get("error") or "")[:500]}
    if t == "patch":
        return {**base, "files": [os.path.basename(f) for f in p.get("files") or []][:20]}
    if t == "file":
        return {**base, "name": p.get("filename") or p.get("url") or ""}
    return None             # step-start/finish, snapshots, agents: bookkeeping


def message_view(m):
    info = m.get("info") or {}
    err = info.get("error")
    return {"id": info.get("id"), "role": info.get("role"),
            "parts": [v for v in (part_view(p) for p in m.get("parts") or []) if v],
            "error": (err.get("data", {}).get("message") or err.get("name")) if isinstance(err, dict) else None,
            "model": info.get("modelID")}


def event_view(payload, session_id):
    """One of OpenCode's events, cut to what the open session needs; None to drop it."""
    t = payload.get("type") or ""
    p = payload.get("properties") or {}
    sid = p.get("sessionID") or (p.get("info") or {}).get("sessionID") or (p.get("part") or {}).get("sessionID")
    if sid != session_id:
        return None
    if t == "message.part.delta":
        return {"t": "delta", "mid": p.get("messageID"), "pid": p.get("partID"), "field": p.get("field"),
                "delta": p.get("delta") or ""}
    if t == "message.part.updated":
        view = part_view(p.get("part") or {})
        return {"t": "part", "mid": (p.get("part") or {}).get("messageID"), "part": view} if view else None
    if t == "message.updated":
        return {"t": "message", "message": message_view({"info": p.get("info") or {}, "parts": []})}
    if t == "session.status":
        return {"t": "status", "status": (p.get("status") or {}).get("type") or "idle"}
    if t == "session.idle":
        return {"t": "status", "status": "idle"}
    if t == "session.error":
        err = p.get("error") or {}
        return {"t": "error", "message": (err.get("data") or {}).get("message") or err.get("name") or "errore"}
    if t.startswith("permission."):
        return {"t": "permissions"}          # the phone re-reads the list
    if t == "session.updated":
        return {"t": "session", "session": session_row(p.get("info") or {})}
    return None


def permission_view(p):
    meta = p.get("metadata") or {}
    return {"id": p.get("id"), "permission": p.get("permission") or p.get("type") or "",
            "patterns": p.get("patterns") or [], "title": p.get("title") or "",
            "command": str(meta.get("command") or meta.get("filepath") or meta.get("filePath") or "")[:1000]}


# ── sessions ──────────────────────────────────────────────────────
@router.get("/api/code/places")
async def places():
    """Folders worth offering: OpenCode's projects and where sessions already live."""
    projects, sessions = await asyncio.gather(oc("GET", "/project"),
                                              oc("GET", "/experimental/session", params={"roots": "true", "limit": 200}))
    seen = {}
    for s in _items(sessions):
        d = s.get("directory")
        if d:
            upd = ((s.get("time") or {}).get("updated") or 0) / 1000
            prev = seen.get(d, {"dir": d, "sessions": 0, "updated": 0})
            seen[d] = {**prev, "sessions": prev["sessions"] + 1, "updated": max(prev["updated"], upd)}
    for p in projects or []:
        w = p.get("worktree")
        if w and w != "/" and w not in seen:
            seen[w] = {"dir": w, "sessions": 0, "updated": 0}
    return {"places": sorted(seen.values(), key=lambda x: -x["updated"])}


@router.get("/api/code/sessions")
async def sessions(dir: str = "", q: str = ""):
    params = {"roots": "true", "limit": 100}
    if dir:
        params["directory"] = _dir(dir)
    if q.strip():
        params["search"] = q.strip()[:100]
    data = await oc("GET", "/experimental/session", params=params)
    rows = sorted((session_row(s) for s in _items(data)), key=lambda r: -r["updated"])
    return {"sessions": rows}


@router.post("/api/code/sessions")
async def create_session(request: Request):
    body = await request.json()
    d = _dir(body.get("dir") or "")
    s = await oc("POST", "/session", params={"directory": d},
                 payload={"title": str(body.get("title") or "")[:120]} if body.get("title") else {})
    audit.record("pwa", "code-session", f"new {s.get('id')} in {d}")
    return session_row(s)


@router.get("/api/code/sessions/{sid}")
async def session_detail(sid: str, dir: str):
    sid, d = _id(sid), _dir(dir)
    info, messages, status, perms = await asyncio.gather(
        oc("GET", f"/session/{sid}", params={"directory": d}),
        oc("GET", f"/session/{sid}/message", params={"directory": d}),
        oc("GET", "/session/status", params={"directory": d}),
        oc("GET", "/permission", params={"directory": d}))
    return {"session": session_row(info), "messages": [message_view(m) for m in messages or []],
            "status": ((status or {}).get(sid) or {}).get("type") or "idle",
            "permissions": [permission_view(p) for p in perms or [] if p.get("sessionID") == sid]}


@router.post("/api/code/sessions/{sid}/prompt")
async def prompt(sid: str, request: Request):
    sid = _id(sid)
    body = await request.json()
    d, text = _dir(body.get("dir") or ""), str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "messaggio vuoto"}, status_code=400)
    if len(text) > MAX_PROMPT:
        return JSONResponse({"error": "messaggio troppo lungo"}, status_code=413)
    await oc("POST", f"/session/{sid}/prompt_async", params={"directory": d},
             payload={"parts": [{"type": "text", "text": text}]})
    audit.record("pwa", "code-prompt", f"session={sid} chars={len(text)}")
    return {"ok": True}


@router.post("/api/code/sessions/{sid}/abort")
async def abort(sid: str, request: Request):
    sid = _id(sid)
    d = _dir((await request.json()).get("dir") or "")
    await oc("POST", f"/session/{sid}/abort", params={"directory": d})
    audit.record("pwa", "code-abort", f"session={sid}")
    return {"ok": True}


# "always" is left out, as for Hermes: a standing rule is not a decision for a phone.
PERMISSION_REPLIES = {"once", "reject"}


@router.post("/api/code/sessions/{sid}/permissions/{pid}")
async def permission_reply(sid: str, pid: str, request: Request):
    sid, pid = _id(sid), _id(pid)
    body = await request.json()
    d, response = _dir(body.get("dir") or ""), body.get("response")
    if response not in PERMISSION_REPLIES:
        return JSONResponse({"error": "risposta: once o reject"}, status_code=400)
    await oc("POST", f"/session/{sid}/permissions/{pid}", params={"directory": d}, payload={"response": response})
    audit.record("pwa", "code-permission", f"session={sid} {pid} {response}")
    return {"ok": True}


@router.get("/api/code/sessions/{sid}/events")
async def session_events(sid: str):
    sid = _id(sid)

    async def stream():
        yield sse({"t": "hello"})
        c = client(timeout=httpx.Timeout(15, read=None))
        try:
            async with c.stream("GET", "/global/event") as r:
                if r.status_code != 200:
                    yield sse({"t": "error", "message": f"OpenCode: HTTP {r.status_code}"})
                    return
                lines = r.aiter_lines()
                while True:
                    try:
                        line = await asyncio.wait_for(lines.__anext__(), KEEPALIVE_S)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    except StopAsyncIteration:
                        return
                    if not line.startswith("data:"):
                        continue
                    try:
                        raw = json.loads(line[5:])
                    except ValueError:
                        continue
                    view = event_view(raw.get("payload") or raw, sid)
                    if view:
                        yield sse(view, pad=view["t"] != "delta")
        except httpx.HTTPError as e:
            yield sse({"t": "error", "message": f"OpenCode non risponde: {e.__class__.__name__}"})
        finally:
            await c.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── terminals ─────────────────────────────────────────────────────
# OpenCode's PTYs add a login flag of their own to the command; given to
# `opencode` itself that is an unknown option and it quits at once. So it
# runs from a login shell, which also leaves a prompt when OpenCode exits.
TERMINALS = {
    "shell": {"command": "bash", "args": [], "title": "Terminale"},
    "opencode": {"command": "bash", "args": ["-lc", "opencode; exec bash -l"], "title": "OpenCode"},
}


def pty_row(p):
    return {"id": p.get("id"), "title": p.get("title") or "", "command": p.get("command") or "",
            "dir": p.get("cwd") or "", "running": p.get("status") == "running"}


@router.get("/api/code/pty")
async def pty_list():
    return {"terminals": [pty_row(p) for p in await oc("GET", "/pty") or []]}


@router.post("/api/code/pty")
async def pty_create(request: Request):
    body = await request.json()
    kind = TERMINALS.get(body.get("kind"))
    if not kind:
        return JSONResponse({"error": "tipo: shell o opencode"}, status_code=400)
    d = _dir(body.get("dir") or "/root")
    p = await oc("POST", "/pty", payload={
        "command": kind["command"], "args": kind["args"], "cwd": d,
        "title": f"{kind['title']} · {os.path.basename(d) or d}",
        "env": {"TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": "C.UTF-8"}})
    audit.record("pwa", "code-terminal", f"{body.get('kind')} in {d} ({p.get('id')})")
    return pty_row(p)


@router.put("/api/code/pty/{pid}/size")
async def pty_size(pid: str, request: Request):
    body = await request.json()
    rows, cols = int(body.get("rows") or 0), int(body.get("cols") or 0)
    if not (2 <= rows <= 300 and 10 <= cols <= 500):
        return JSONResponse({"error": "dimensioni non valide"}, status_code=400)
    await oc("PUT", f"/pty/{_id(pid)}", payload={"size": {"rows": rows, "cols": cols}})
    return {"ok": True}


@router.delete("/api/code/pty/{pid}")
async def pty_close(pid: str):
    await oc("DELETE", f"/pty/{_id(pid)}")
    audit.record("pwa", "code-terminal", f"chiuso {pid}")
    return {"ok": True}


async def ws_connect(url, headers):
    """Opened here so the tests can put something else in its place."""
    from websockets.asyncio.client import connect
    return await connect(url, additional_headers=headers, max_size=2 ** 22, open_timeout=10)


def ws_allowed(ws: WebSocket):
    """The HTTP gate never sees WebSockets: the same checks, by hand."""
    if not config.VOICE_AUTH_TOKEN or not security.cookie_ok(ws.cookies.get(security.COOKIE)):
        return 4401
    if not passkey.unlocked_until(ws):
        return 4403
    origin = ws.headers.get("origin") or ""
    hosts = {ws.headers.get("host", ""), ws.headers.get("x-forwarded-host", "")} - {""}
    if not origin or origin.split("://", 1)[-1] not in hosts:
        return 4403        # another site's page opening a socket with our cookies
    return None


@ws_router.websocket("/api/code/pty/{pid}/ws")
async def pty_socket(ws: WebSocket, pid: str, cursor: int = -1):
    refused = ws_allowed(ws)
    if refused or not ID.match(pid):
        await ws.close(code=refused or 4400)
        return
    await ws.accept()
    auth = base64.b64encode(f"opencode:{config.OPENCODE_PASSWORD}".encode()).decode()
    url = config.OPENCODE_URL.replace("http", "ws", 1) + f"/pty/{pid}/connect" + (f"?cursor={cursor}" if cursor >= 0 else "")
    try:
        upstream = await ws_connect(url, {"Authorization": f"Basic {auth}"})
    except Exception as e:  # noqa: BLE001 — whatever the reason, the phone must see it
        await ws.send_text(f"\r\n\x1b[31mTerminale non raggiungibile: {e.__class__.__name__}\x1b[0m\r\n")
        await ws.close(code=4404)
        return
    started = time.time()

    async def down():
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_text(msg)

    async def up():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("text") if msg.get("text") is not None else msg.get("bytes")
            if data:
                await upstream.send(data)

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await upstream.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001 — the phone already went away
            pass
        audit.record("pwa", "code-terminal", f"collegato a {pid} per {int(time.time() - started)} s")
