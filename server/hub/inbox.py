"""The Inbox tab: your chats on every network, through Beeper.

Beeper Desktop runs on this server with no screen (deploy/beeper/). Its chats
are connected "on device", so the bridges to WhatsApp, Instagram, LinkedIn...
run here, and its local API (127.0.0.1:23373) serves them. This module turns
that API into what a phone draws, and follows its live events to notify new
messages.

The hub reaches the API with an OAuth token that Beeper grants only once you
approve the request in Beeper's own window (beeper-screen.service). It is kept
in hub.db; the phone never sees it.

What leaves from here reaches other people: a message sent, and the read
receipt of opening a chat. Both follow a tap in the app, and sends are audited.
"""
import asyncio
import base64
import collections
import contextlib
import hashlib
import json
import posixpath
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from . import audit, config, push
from .speech import sse

transport = None            # tests swap in an httpx.MockTransport
ws_open = None              # tests swap in a fake socket; see Watcher.connect
CLIENT_NAME = "Hermes Hub"
# Never opened: Beeper hands the code back in its reply, the redirect is only
# part of the OAuth handshake.
REDIRECT = "http://127.0.0.1/hermes-hub/beeper"
# Beeper keeps its approval window open while the request waits.
APPROVE_S = 600
PAGE = 50
MAX_TEXT = 10000
# Older than this, a message arriving is history being synced, not news.
NOTIFY_WINDOW_S = 300
# One notification per chat in this span: a busy group should not ring on
# every line.
NOTIFY_GAP_S = 20
KEEPALIVE_S = 15
CHAT_ID = re.compile(r"^[^\s/\\?#]{1,300}$")

KIND_LABEL = {
    "IMAGE": "📷 Foto", "VIDEO": "🎥 Video", "VOICE": "🎤 Messaggio vocale", "AUDIO": "🎵 Audio",
    "FILE": "📎 File", "STICKER": "Sticker", "LOCATION": "📍 Posizione",
}

router = APIRouter()


class InboxError(Exception):
    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


# ── state: the token, and whether new messages ring ───────────────
_lock = threading.Lock()
_ready = set()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    client_id     TEXT,
    token         TEXT,
    token_made    REAL,
    token_expires REAL,
    notify        INTEGER NOT NULL DEFAULT 1
)"""
_FIELDS = ("client_id", "token", "token_made", "token_expires", "notify")


def _connect():
    conn = sqlite3.connect(config.HUB_DB)
    if config.HUB_DB not in _ready:
        conn.execute(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO inbox (id) VALUES (1)")
        _ready.add(config.HUB_DB)
    return conn


def _row():
    with _lock, _connect() as conn:
        r = conn.execute("SELECT " + ", ".join(_FIELDS) + " FROM inbox").fetchone()
    return dict(zip(_FIELDS, r))


def _update(**fields):
    with _lock, _connect() as conn:
        conn.execute("UPDATE inbox SET " + ", ".join(f"{k} = ?" for k in fields), tuple(fields.values()))


# ── Beeper's API ──────────────────────────────────────────────────
def client(timeout=15):
    return httpx.AsyncClient(base_url=config.BEEPER_URL, transport=transport, timeout=timeout)


def _message(r):
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        err = err.get("message")
    return str(body.get("message") or body.get("error_description") or err or r.text)[:200]


async def bp(method, path, params=None, payload=None, form=None, timeout=15, auth=True):
    headers = {}
    if auth:
        token = _row()["token"]
        if not token:
            raise InboxError("l'Hub non è ancora collegato a Beeper", 409, "connect")
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with client(timeout) as c:
            r = await c.request(method, path, params=params, json=payload, data=form, headers=headers)
    except httpx.HTTPError as e:
        raise InboxError(f"Beeper non risponde: {e.__class__.__name__}", 503, "down") from e
    if r.status_code == 401:
        # Without a token too: once signed in, Beeper answers nothing to strangers.
        raise InboxError("l'accesso a Beeper è scaduto o revocato: ricollega l'Hub" if auth
                         else "Beeper chiede che l'Hub sia collegato", 409, "connect")
    if r.status_code >= 400:
        raise InboxError(f"Beeper: {_message(r)}", 404 if r.status_code == 404 else 502)
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def _path_id(cid):
    if not isinstance(cid, str) or not CHAT_ID.match(cid):
        raise InboxError("chat non valida", 400)
    return quote(cid, safe="")


def _ts(iso):
    """Beeper's ISO timestamps as epoch seconds, what the app's clock() reads."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ── what the phone draws ──────────────────────────────────────────
def asset_ok(url):
    """Media the hub may fetch from Beeper for the phone: Beeper's own, never any file."""
    if not isinstance(url, str) or len(url) > 2000:
        return False
    if url.startswith(("mxc://", "localmxc://")):
        return True
    if url.startswith("file://"):
        path = posixpath.normpath(unquote(urlsplit(url).path))
        return path.startswith(config.BEEPER_FILES.rstrip("/") + "/")
    return False


def asset_url(url):
    if not url:
        return None
    if url.startswith(("https://", "data:image/")):
        return url
    if asset_ok(url):
        return "/api/inbox/asset?u=" + quote(url, safe="")
    return None


def attachment_view(a):
    size = a.get("size") or {}
    return {
        "kind": a.get("type") or "unknown",
        "url": asset_url(a.get("srcURL") or a.get("id")),
        "mime": a.get("mimeType"),
        "name": a.get("fileName"),
        "bytes": a.get("fileSize"),
        "w": size.get("width"), "h": size.get("height"),
        "voice": bool(a.get("isVoiceNote")),
        "gif": bool(a.get("isGif")),
        "sticker": bool(a.get("isSticker")),
        "duration": a.get("duration"),
        "poster": asset_url(a.get("posterImg")),
        "transcript": (a.get("transcription") or {}).get("transcription"),
    }


def reactions_view(reactions):
    counts = collections.OrderedDict()
    for r in reactions or []:
        key = r.get("reactionKey")
        if not key:
            continue
        entry = counts.setdefault(key, {"key": key, "count": 0, "mine": False})
        entry["count"] += 1
        entry["mine"] = entry["mine"] or bool(r.get("isSender"))
    return list(counts.values())


def message_view(m):
    status = (m.get("sendStatus") or {}).get("status")
    return {
        "id": m.get("id"),
        "sender": m.get("senderName") or "",
        "sender_id": m.get("senderID"),
        "mine": bool(m.get("isSender")),
        "time": _ts(m.get("timestamp")),
        "sort": m.get("sortKey"),
        "type": m.get("type") or "TEXT",
        "text": m.get("text") or "",
        "edited": bool(m.get("editedTimestamp")),
        "deleted": bool(m.get("isDeleted")),
        "reply_to": m.get("linkedMessageID"),
        "status": status,
        "failed": (m.get("sendStatus") or {}).get("message") if status and status.startswith("FAIL") else None,
        "attachments": [attachment_view(a) for a in m.get("attachments") or []],
        "reactions": reactions_view(m.get("reactions")),
    }


def shown(m):
    # Reactions arrive as messages of their own; they are drawn on their target.
    return not m.get("isHidden") and m.get("type") != "REACTION"


def preview_text(m):
    if m.get("isDeleted"):
        return "messaggio eliminato"
    text = " ".join((m.get("text") or "").split())
    if text:
        return text[:160]
    return KIND_LABEL.get(m.get("type"), "messaggio")


def chat_view(c):
    p = c.get("preview")
    return {
        "id": c["id"],
        "title": c.get("title") or "?",
        "network": c.get("network") or "",
        "account": c.get("accountID"),
        "group": c.get("type") == "group",
        "unread": c.get("unreadCount") or 0,
        "mentions": c.get("unreadMentionsCount") or 0,
        "marked_unread": bool(c.get("isMarkedUnread")),
        "muted": bool(c.get("isMuted")),
        "pinned": bool(c.get("isPinned")),
        "archived": bool(c.get("isArchived")),
        "readonly": bool(c.get("isReadOnly")),
        "time": _ts(c.get("lastActivity")),
        "avatar": asset_url(c.get("imgURL")),
        "preview": {"text": preview_text(p), "mine": bool(p.get("isSender")),
                    "sender": p.get("senderName") or ""} if p else None,
    }


def account_view(a):
    user = a.get("user") or {}
    bridge = a.get("bridge") or {}
    return {
        "id": a.get("accountID"),
        "network": a.get("network") or bridge.get("name") or bridge.get("type") or "",
        "name": user.get("fullName") or user.get("username") or user.get("phoneNumber") or "",
        "status": a.get("status"),
        "status_text": a.get("statusText") or "",
    }


# ── connecting the hub: OAuth with PKCE, approved in Beeper's window ──
class Connect:
    """One request for access at a time; its outcome stays readable for the app."""

    def __init__(self):
        self.task = None
        self.error = None

    @property
    def pending(self):
        return bool(self.task and not self.task.done())

    def start(self):
        if not self.pending:
            self.error = None
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            await self._flow()
        except InboxError as e:
            self.error = str(e)
        except Exception as e:  # noqa: BLE001 — the app shows it instead of a silent nothing
            self.error = f"collegamento non riuscito: {e.__class__.__name__}"

    async def _flow(self, retried=False):
        client_id = _row()["client_id"]
        if not client_id:
            reg = await bp("POST", "/oauth/register", auth=False,
                           payload={"client_name": CLIENT_NAME, "redirect_uris": [REDIRECT]})
            client_id = reg["client_id"]
            _update(client_id=client_id)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(12)
        # What Beeper's consent page sends when opened: the reply waits for
        # Approve or Decline in Beeper's window.
        try:
            res = await bp("POST", "/oauth/authorize/callback", timeout=APPROVE_S, auth=False, payload={
                "mode": "oauth2",
                "clientInfo": {"name": CLIENT_NAME, "clientURI": None, "clientID": client_id},
                "scopes": ["read", "write"], "scope": "read write",
                "redirectUri": REDIRECT, "state": state,
                "codeChallenge": challenge, "codeChallengeMethod": "S256",
            })
        except InboxError as e:
            # A Beeper reinstalled has forgotten the client: register again, once.
            if "client" in str(e).lower() and not retried:
                _update(client_id=None)
                return await self._flow(retried=True)
            raise
        if not res or not res.get("code"):
            err = (res or {}).get("error")
            raise InboxError("richiesta rifiutata in Beeper" if err in ("access_denied", "denied")
                             else f"Beeper non ha concesso l'accesso ({err or 'nessun codice'})", 403)
        tok = await bp("POST", "/oauth/token", auth=False, form={
            "grant_type": "authorization_code", "code": res["code"],
            "code_verifier": verifier, "client_id": client_id, "redirect_uri": REDIRECT,
        })
        now = time.time()
        expires = tok.get("expires_in")
        _update(token=tok["access_token"], token_made=now, token_expires=now + expires if expires else None)
        audit.record("pwa", "inbox-connect", f"Beeper ha concesso l'accesso ({tok.get('scope', '')})")


connector = Connect()


# ── live events and notifications ─────────────────────────────────
class Subscriber:
    def __init__(self, chat=None):
        self.chat = chat
        self.queue = asyncio.Queue(maxsize=200)


class Watcher:
    """Beeper's event stream, shared: fans out to the open app and rings the phone."""

    def __init__(self):
        self.subscribers = set()
        self.connected = False
        self.notified = collections.OrderedDict()   # message ids already rung for
        self.last_ring = {}                           # chat id -> time
        self.chats = {}                               # chat id -> (fetched, chat)

    def publish(self, item):
        for s in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                s.queue.put_nowait(item)

    @contextlib.asynccontextmanager
    async def connect(self, token):
        if ws_open:
            async with ws_open(token) as ws:
                yield ws
            return
        from websockets.asyncio.client import connect
        url = config.BEEPER_URL.replace("http", "ws", 1) + "/v1/ws"
        async with connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                           open_timeout=15, ping_interval=30, max_size=8 * 1024 * 1024) as ws:
            yield ws

    async def run(self):
        backoff = 5
        while True:
            token = _row()["token"]
            if not token:
                await asyncio.sleep(20)
                continue
            try:
                async with self.connect(token) as ws:
                    await ws.send('{"type":"subscriptions.set","requestID":"hub","chatIDs":["*"]}')
                    self.connected = True
                    self.publish({"t": "live", "on": True})
                    backoff = 5
                    async for raw in ws:
                        await self.handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — Beeper restarting must not end the watcher
                print(f"[INBOX] eventi di Beeper: {e.__class__.__name__}: {str(e)[:160]}")
            if self.connected:
                self.connected = False
                self.publish({"t": "live", "on": False})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def handle(self, raw):
        try:
            ev = json.loads(raw)
        except (TypeError, ValueError):
            return
        kind, chat = ev.get("type"), ev.get("chatID")
        if kind == "message.upserted":
            for m in ev.get("entries") or []:
                if isinstance(m, dict) and m.get("id"):
                    if shown(m):
                        self.publish({"t": "message", "chat": chat, "message": message_view(m)})
                    await self.maybe_ring(chat, m)
            if not ev.get("entries"):
                self.publish({"t": "changed", "chat": chat})
        elif kind == "message.deleted":
            self.publish({"t": "deleted", "chat": chat, "ids": ev.get("ids") or []})
        elif kind in ("chat.upserted", "chat.deleted"):
            self.chats.pop(chat, None)
            self.publish({"t": "chat", "chat": chat})
        elif kind == "app.state.updated":
            self.publish({"t": "state", "state": (ev.get("appState") or {}).get("state")})

    async def chat(self, chat_id):
        hit = self.chats.get(chat_id)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        try:
            c = await bp("GET", f"/v1/chats/{_path_id(chat_id)}")
        except InboxError:
            return None
        self.chats[chat_id] = (time.time(), c)
        return c

    async def maybe_ring(self, chat_id, m):
        now = time.time()
        if not _row()["notify"] or m.get("isSender") or not shown(m) or m.get("isDeleted"):
            return
        mid = m["id"]
        if mid in self.notified:           # an edit or a delivery update of one already rung
            return
        self.notified[mid] = now
        while len(self.notified) > 2000:
            self.notified.popitem(last=False)
        sent = _ts(m.get("timestamp"))
        if sent is None or now - sent > NOTIFY_WINDOW_S:
            return
        if any(s.chat == chat_id for s in self.subscribers):   # open on the phone right now
            return
        if now - self.last_ring.get(chat_id, 0) < NOTIFY_GAP_S:
            return
        chat = await self.chat(chat_id)
        if not chat or chat.get("isMuted") or chat.get("isLowPriority") or chat.get("isArchived"):
            return
        self.last_ring[chat_id] = now
        title = chat.get("title") or m.get("senderName") or "Messaggio"
        if chat.get("type") == "group" and m.get("senderName"):
            title = f"{m['senderName']} · {title}"
        network = chat.get("network")
        await push.send_all(f"{title} ({network})" if network else title, preview_text(m),
                            tag=f"inbox:{chat_id[:80]}", url="/#/inbox/" + quote(chat_id, safe=""))


watcher = Watcher()


# ── routes ────────────────────────────────────────────────────────
@router.get("/api/inbox/status")
async def status():
    row = _row()
    out = {"beeper": "up", "setup": None, "connected": False, "pending": connector.pending,
           "error": connector.error, "expires": row["token_expires"], "accounts": [],
           "notify": bool(row["notify"]), "live": watcher.connected}
    try:
        setup = await bp("GET", "/v1/app/setup", auth=bool(row["token"]))
        out["setup"] = (setup or {}).get("state")
    except InboxError as e:
        if e.code == "down":
            out["beeper"] = "down"
            return out
        if e.code == "connect":
            # Signed in, but this hub's token is gone: public only before sign-in.
            out["setup"] = "ready"
            return out
        out["setup"] = "unknown"
    if row["token"]:
        try:
            accounts = await bp("GET", "/v1/accounts")
            items = accounts if isinstance(accounts, list) else (accounts or {}).get("items", [])
            out["accounts"] = [account_view(a) for a in items]
            out["connected"] = True
        except InboxError as e:
            if e.code != "connect":
                out["error"] = out["error"] or str(e)
    return out


@router.post("/api/inbox/connect")
async def connect():
    connector.start()
    return {"pending": True}


@router.delete("/api/inbox/connect")
async def disconnect():
    token = _row()["token"]
    if token:
        with contextlib.suppress(InboxError):
            await bp("POST", "/oauth/revoke", auth=False, form={"token": token})
    _update(token=None, token_made=None, token_expires=None)
    audit.record("pwa", "inbox-disconnect", "accesso a Beeper revocato")
    return {"ok": True}


@router.post("/api/inbox/notify")
async def set_notify(request: Request):
    body = await request.json()
    _update(notify=1 if body.get("on") else 0)
    return {"notify": bool(body.get("on"))}


@router.get("/api/inbox/chats")
async def chats(q: str = "", unread: bool = False, account: str = "", cursor: str = ""):
    params = {"limit": PAGE, "inbox": "primary"}
    if q.strip():
        params["query"] = q.strip()[:200]
    if unread:
        params["unreadOnly"] = "true"
    if account:
        params["accountIDs"] = [account]
    if cursor:
        params.update(cursor=cursor, direction="before")
    if q.strip() or unread:
        data = await bp("GET", "/v1/chats/search", params=params)
    else:
        params.pop("inbox")
        data = await bp("GET", "/v1/chats", params=params)
    items = [c for c in data.get("items", []) if not c.get("mergedIntoChatID") and not c.get("isArchived")]
    return {"chats": [chat_view(c) for c in items],
            "older": data.get("oldestCursor") if data.get("hasMore") else None}


@router.get("/api/inbox/chats/{cid}")
async def chat_detail(cid: str, cursor: str = ""):
    path = _path_id(cid)
    params = {"cursor": cursor, "direction": "before"} if cursor else None
    data = await bp("GET", f"/v1/chats/{path}/messages", params=params)
    chat = None if cursor else chat_view(await bp("GET", f"/v1/chats/{path}"))
    items = sorted((m for m in data.get("items", []) if shown(m)),
                   key=lambda m: (m.get("timestamp") or "", m.get("sortKey") or ""))
    return {"chat": chat, "messages": [message_view(m) for m in items],
            "older": data.get("oldestCursor") if data.get("hasMore") else None}


@router.post("/api/inbox/chats/{cid}/messages")
async def send(cid: str, request: Request):
    path = _path_id(cid)
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        raise InboxError("messaggio vuoto", 400)
    if len(text) > MAX_TEXT:
        raise InboxError("messaggio troppo lungo", 413)
    payload = {"text": text}
    reply = body.get("reply_to")
    if isinstance(reply, str) and reply:
        payload["replyToMessageID"] = reply[:200]
    res = await bp("POST", f"/v1/chats/{path}/messages", payload=payload, timeout=30)
    audit.record("pwa", "inbox-send", f"chat={cid[:80]} chars={len(text)}")
    return {"ok": True, "pending_id": (res or {}).get("pendingMessageID")}


@router.post("/api/inbox/chats/{cid}/read")
async def mark_read(cid: str, request: Request):
    path = _path_id(cid)
    body = await request.json()
    mid = body.get("message_id")
    await bp("POST", f"/v1/chats/{path}/read", payload={"messageID": mid} if isinstance(mid, str) and mid else {})
    return {"ok": True}


@router.get("/api/inbox/asset")
async def asset(u: str, request: Request):
    if not asset_ok(u):
        raise InboxError("file non disponibile", 400)
    token = _row()["token"]
    if not token:
        raise InboxError("l'Hub non è ancora collegato a Beeper", 409, "connect")
    headers = {"Authorization": f"Bearer {token}"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]     # voice notes and videos seek
    c = client(timeout=httpx.Timeout(30, read=120))
    try:
        r = await c.send(c.build_request("GET", "/v1/assets/serve", params={"url": u}, headers=headers), stream=True)
    except httpx.HTTPError as e:
        await c.aclose()
        raise InboxError(f"Beeper non risponde: {e.__class__.__name__}", 503, "down") from e
    if r.status_code >= 400:
        await r.aclose()
        await c.aclose()
        raise InboxError("file non disponibile", 404 if r.status_code == 404 else 502)

    async def body():
        try:
            async for chunk in r.aiter_bytes():
                yield chunk
        finally:
            await r.aclose()
            await c.aclose()

    # Sent decoded: a length Beeper gave for compressed bytes would be wrong.
    keep = ("content-range", "accept-ranges")
    if "content-encoding" not in r.headers:
        keep += ("content-length",)
    passed = {k: r.headers[k] for k in keep if k in r.headers}
    return StreamingResponse(body(), status_code=r.status_code,
                             media_type=r.headers.get("content-type", "application/octet-stream"),
                             headers={**passed, "Cache-Control": "private, max-age=86400"})


@router.get("/api/inbox/events")
async def events(chat: str = ""):
    if chat and not CHAT_ID.match(chat):
        raise InboxError("chat non valida", 400)
    sub = Subscriber(chat or None)
    watcher.subscribers.add(sub)

    async def stream():
        try:
            yield sse({"t": "hello", "live": watcher.connected})
            while True:
                try:
                    item = await asyncio.wait_for(sub.queue.get(), KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                # A chat open on the phone only needs its own events.
                if sub.chat and item.get("chat") not in (None, sub.chat):
                    continue
                yield sse(item)
        finally:
            watcher.subscribers.discard(sub)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
