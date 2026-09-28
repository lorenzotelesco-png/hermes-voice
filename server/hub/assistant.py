"""Hey Hermes: Hermes from an iOS Shortcut, without opening the app.

No iPhone app may listen for a wake word, but iOS's Vocal Shortcuts
(Settings > Accessibility) run a Shortcut on a phrase of your choosing, with
the phone locked too. That Shortcut dictates, posts the text here, and speaks
the answer; say the phrase again, or keep talking, and the conversation goes
on in the same Hermes session, which the app lists like any other.

The Shortcut carries its own key, never the app's: a Shortcut syncs through
iCloud and can be shared by mistake, and a key leaked from there must reach
Hermes' voice and nothing else — no files, no server, no terminal. The key is
minted in the app, shown once, and kept here only as a hash.

Anything that needs the screen does not wait on the Shortcut: an approval, or
a task longer than a Shortcut will wait for, ends the call with a sentence to
say, and the result follows as a notification that opens the app on it.
"""
import asyncio
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import audit, config, hermes, push, runs
from .speech import VOICE_SYSTEM_PROMPT, clean_for_tts

router = APIRouter()
ASK_PATH = "/api/assistant/ask"

# Past this, the next "Hey Hermes" starts a new conversation.
CONTINUE_S = 600
# Shortcuts gives up on a request after about a minute; answer well before.
WAIT_S = 40
MAX_CHARS = 2000
# A leaked key must not be able to run up the bill.
RATE_WINDOW_S, RATE_MAX = 600, 40
# Said after a turn to end the conversation (compared without punctuation).
STOP_WORDS = {"grazie", "grazie mille", "ok grazie", "basta", "stop", "fine", "niente", "a posto",
              "ciao", "arrivederci", "no grazie", "è tutto", "e tutto", "ho finito"}

_lock = threading.Lock()
_ready = set()
_recent = []
_waiters = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    key_hash  TEXT,
    key_made  REAL,
    key_used  REAL,
    session   TEXT,
    last_turn REAL
)"""


def _connect():
    conn = sqlite3.connect(config.HUB_DB)
    if config.HUB_DB not in _ready:
        conn.execute(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO assistant (id) VALUES (1)")
        _ready.add(config.HUB_DB)
    return conn


def _row():
    with _lock, _connect() as conn:
        r = conn.execute("SELECT key_hash, key_made, key_used, session, last_turn FROM assistant").fetchone()
    return dict(zip(("key_hash", "key_made", "key_used", "session", "last_turn"), r))


def _update(**fields):
    with _lock, _connect() as conn:
        conn.execute("UPDATE assistant SET " + ", ".join(f"{k} = ?" for k in fields), tuple(fields.values()))


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def key_ok(token):
    stored = _row()["key_hash"]
    return bool(token and stored and hmac.compare_digest(_hash(token), stored))


def _rate_ok(now):
    _recent[:] = [t for t in _recent if now - t < RATE_WINDOW_S]
    if len(_recent) >= RATE_MAX:
        return False
    _recent.append(now)
    return True


def _is_stop(text):
    # Dictation punctuates freely: "Ok, grazie." is still a goodbye.
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split()) in STOP_WORDS


async def _wait(run, timeout, after=-1):
    """("done", event) | ("approval", event) | ("timeout", None), for events after `after`."""
    async def follow():
        async for batch in run.follow(after, keepalive=5):
            for ev in batch:
                if ev["type"] in ("approval", "done"):
                    return ev["type"], ev
        return "done", {"reply": "", "status": "failed"}
    try:
        return await asyncio.wait_for(follow(), timeout)
    except asyncio.TimeoutError:
        return "timeout", None


async def _approval_notice(run, session_id, ev):
    # Opens the app on this very turn, where the approval sheet is waiting.
    await push.send_all("Hermes chiede una conferma",
                        (ev.get("command") or ev.get("description") or "")[:160],
                        tag=f"ask:{session_id}", url=f"/#/chat/turno/{run.id}/{session_id}")


async def _notify_when_done(run, session_id, after=-1):
    """Tell the phone how a turn the Shortcut could not wait for goes on, and ends."""
    while True:
        kind, ev = await _wait(run, 3600, after)
        if kind != "approval":
            break
        await _approval_notice(run, session_id, ev)
        after = ev["seq"]
    if kind != "done":
        return
    reply = clean_for_tts(ev.get("reply") or "") or "Ho finito."
    await push.send_all("Hermes ha risposto", reply[:180], tag=f"ask:{session_id}",
                        url=f"/#/chat/apri/{session_id}")


def _later(coro):
    task = asyncio.create_task(coro)
    _waiters.add(task)
    task.add_done_callback(_waiters.discard)


async def ask(text, new=False):
    text = (text or "").strip()
    if not text:
        return {"reply": "Non ho sentito niente.", "end": 1}
    if _is_stop(text):
        return {"reply": "A dopo.", "end": 1}
    now = time.time()
    state = _row()
    session_id = state["session"]
    if new or not session_id or now - (state["last_turn"] or 0) > CONTINUE_S:
        session_id = (await hermes.api_request("POST", "/api/sessions", payload={}))["session"]["id"]
    _update(session=session_id, last_turn=now, key_used=now)
    audit.record("shortcut", "ask", f"session={session_id} chars={len(text)}")

    run = await runs.start(session_id, text[:MAX_CHARS], True, VOICE_SYSTEM_PROMPT)
    kind, ev = await _wait(run, WAIT_S)
    _update(last_turn=time.time())
    if kind == "done":
        reply = clean_for_tts(ev.get("reply") or "")
        if ev.get("status") != "completed" and not reply:
            reply = "Qualcosa è andato storto: trovi i dettagli nell'app."
        return {"reply": reply or "Fatto.", "end": 0, "session_id": session_id}

    # From here the answer comes later, by notification.
    reachable = bool(push.subscriptions())
    if kind == "approval":
        if reachable:
            await _approval_notice(run, session_id, ev)
        _later(_notify_when_done(run, session_id, after=ev["seq"]))
        reply = ("Mi serve una conferma: ti ho mandato una notifica." if reachable
                 else "Mi serve una conferma: apri l'app Hermes per rispondere.")
    else:
        _later(_notify_when_done(run, session_id))
        reply = ("Ci sto ancora lavorando: ti mando una notifica quando ho finito." if reachable
                 else "Ci sto ancora lavorando: trovi la risposta nell'app.")
    return {"reply": reply, "end": 1, "session_id": session_id}


# ── routes ────────────────────────────────────────────────────────
def _error(message, status):
    return JSONResponse({"error": message}, status_code=status)


@router.post(ASK_PATH)
async def ask_route(request: Request):
    # Reached with the Shortcut's key (security.check lets only this path
    # through on it) or from the app with its cookie, to try it out.
    if not _rate_ok(time.time()):
        return _error("troppe richieste: riprova tra qualche minuto", 429)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        return await ask(str(body.get("text") or ""), new=bool(body.get("new")))
    except hermes.HermesError as e:
        # Still something to say: the Shortcut speaks whatever comes back.
        return JSONResponse({"reply": "Non riesco a raggiungere Hermes in questo momento.", "end": 1,
                             "error": str(e)}, status_code=200)


@router.get("/api/assistant/key")
async def key_status():
    r = _row()
    return {"configured": bool(r["key_hash"]), "created": r["key_made"], "last_used": r["key_used"],
            "path": ASK_PATH}


@router.post("/api/assistant/key")
async def key_mint():
    token = secrets.token_urlsafe(32)
    _update(key_hash=_hash(token), key_made=time.time(), key_used=None)
    audit.record("pwa", "assistant-key", "nuova chiave per la Scorciatoia")
    # The only time it is ever shown.
    return {"key": token, "path": ASK_PATH}


@router.delete("/api/assistant/key")
async def key_revoke():
    _update(key_hash=None, key_made=None, key_used=None)
    audit.record("pwa", "assistant-key", "chiave revocata")
    return {"ok": True}
