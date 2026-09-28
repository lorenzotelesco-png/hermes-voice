"""Face ID in front of the Code tab: WebAuthn passkeys, verified here.

The Code tab is a root shell and an agent that runs bash without asking. The
app's token alone — which lives in a cookie, and once in a URL — must not be
enough for that, so opening it takes a passkey: Face ID on the phone, the key
never leaving its secure enclave. An unlock lasts UNLOCK_S, in a cookie of its
own that only /api/code/* ever sees.

The first passkey can be registered by whoever holds the app's token (that is
the person setting it up, right after deploying); every later one needs an
unlock first, so a stolen token cannot enrol a new face. Lost the phone?
Remove the rows from hub.db over SSH and register again.

Only what an iPhone produces is accepted: "none" attestation, ES256 keys,
user verification required. The CBOR decoder below reads exactly that much.
"""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import audit, config

router = APIRouter()
COOKIE = "hv_code"
COOKIE_PATH = "/api/code"
UNLOCK_S = 30 * 60
CHALLENGE_S = 300

_lock = threading.Lock()
_ready = set()
_challenges = {}        # challenge (b64u) -> (purpose, expires)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS passkeys (
    id         TEXT PRIMARY KEY,
    rp_id      TEXT NOT NULL,
    x          TEXT NOT NULL,
    y          TEXT NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    label      TEXT NOT NULL DEFAULT '',
    created    REAL NOT NULL,
    last_used  REAL
)"""


class Refused(Exception):
    pass


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _connect():
    conn = sqlite3.connect(config.HUB_DB)
    if config.HUB_DB not in _ready:
        conn.execute(_SCHEMA)
        _ready.add(config.HUB_DB)
    return conn


def passkeys():
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT id, rp_id, x, y, sign_count, label, created, last_used FROM passkeys").fetchall()
    return [dict(zip(("id", "rp_id", "x", "y", "sign_count", "label", "created", "last_used"), r)) for r in rows]


# ── CBOR, as much as WebAuthn needs ───────────────────────────────
def cbor(data, i=0):
    """(value, next index). Definite lengths only, which is what authenticators send."""
    head = data[i]
    major, info = head >> 5, head & 31
    i += 1
    if info < 24:
        n = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        n = int.from_bytes(data[i:i + size], "big")
        i += size
    else:
        raise Refused("CBOR non supportato")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major == 2:
        return bytes(data[i:i + n]), i + n
    if major == 3:
        return bytes(data[i:i + n]).decode(), i + n
    if major == 4:
        out = []
        for _ in range(n):
            v, i = cbor(data, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = cbor(data, i)
            out[k], i = cbor(data, i)
        return out, i
    if major == 7 and n in (20, 21, 22):
        return {20: False, 21: True, 22: None}[n], i
    raise Refused("CBOR non supportato")


def parse_auth_data(auth):
    if len(auth) < 37:
        raise Refused("authenticatorData troppo corto")
    out = {"rp_id_hash": auth[:32], "flags": auth[32], "sign_count": int.from_bytes(auth[33:37], "big")}
    if out["flags"] & 0x40:                  # attested credential data follows
        n = int.from_bytes(auth[53:55], "big")
        out["cred_id"] = auth[55:55 + n]
        out["cose"], _ = cbor(auth, 55 + n)
    return out


def _check_client_data(raw, kind, purpose, rp_id):
    try:
        client = json.loads(raw)
    except ValueError:
        raise Refused("clientDataJSON non valido")
    if client.get("type") != kind:
        raise Refused("tipo di richiesta sbagliato")
    expected = _challenges.pop(client.get("challenge") or "", None)
    if not expected or expected[0] != purpose or expected[1] < time.time():
        raise Refused("richiesta scaduta: riprova")
    if client.get("origin") != f"https://{rp_id}":
        raise Refused("origine sbagliata")


def _check_flags(auth, rp_id):
    if auth["rp_id_hash"] != hashlib.sha256(rp_id.encode()).digest():
        raise Refused("passkey di un altro sito")
    if auth["flags"] & 0x05 != 0x05:        # user present + user verified (Face ID)
        raise Refused("serve Face ID")


def rp_id_of(request):
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0]


def new_challenge(purpose):
    now = time.time()
    for c, (_, exp) in list(_challenges.items()):
        if exp < now:
            _challenges.pop(c, None)
    c = b64u(secrets.token_bytes(32))
    _challenges[c] = (purpose, now + CHALLENGE_S)
    return c


def register(rp_id, client_data, attestation, label=""):
    _check_client_data(client_data, "webauthn.create", "register", rp_id)
    obj, _ = cbor(attestation)
    auth = parse_auth_data(obj.get("authData") or b"")
    _check_flags(auth, rp_id)
    cose = auth.get("cose") or {}
    if cose.get(1) != 2 or cose.get(3) != -7 or cose.get(-1) != 1:
        raise Refused("solo chiavi ES256 (come quelle di iPhone)")
    x, y = cose[-2], cose[-3]
    ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO passkeys (id, rp_id, x, y, sign_count, label, created) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (b64u(auth["cred_id"]), rp_id, b64u(x), b64u(y), auth["sign_count"], label[:60], time.time()))
    return b64u(auth["cred_id"])


def verify(rp_id, cred_id, client_data, auth_data, signature):
    key = next((k for k in passkeys() if k["id"] == cred_id), None)
    if not key or key["rp_id"] != rp_id:
        raise Refused("passkey sconosciuta")
    _check_client_data(client_data, "webauthn.get", "unlock", rp_id)
    auth = parse_auth_data(auth_data)
    _check_flags(auth, rp_id)
    public = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64u(key["x"]), "big"),
                                           int.from_bytes(unb64u(key["y"]), "big"), ec.SECP256R1()).public_key()
    try:
        public.verify(signature, auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise Refused("firma non valida")
    # iPhone passkeys always report 0; a counter that goes backwards means a cloned key.
    if key["sign_count"] and auth["sign_count"] <= key["sign_count"]:
        raise Refused("contatore della passkey tornato indietro")
    with _lock, _connect() as conn:
        conn.execute("UPDATE passkeys SET sign_count = ?, last_used = ? WHERE id = ?",
                     (auth["sign_count"], time.time(), cred_id))


# ── the unlock cookie ─────────────────────────────────────────────
def _sign(raw):
    return hmac.new(config.VOICE_AUTH_TOKEN.encode(), ("code:" + raw).encode(), hashlib.sha256).hexdigest()


def unlocked_until(request):
    try:
        raw, sig = (request.cookies.get(COOKIE) or "").split(".", 1)
        exp = int(raw)
    except ValueError:
        return 0
    return exp if exp > time.time() and hmac.compare_digest(sig, _sign(raw)) else 0


def _set_cookie(response, exp):
    raw = str(int(exp))
    response.set_cookie(COOKIE, f"{raw}.{_sign(raw)}", max_age=max(0, int(exp - time.time())), path=COOKIE_PATH,
                        httponly=True, secure=True, samesite="strict")


# ── routes ────────────────────────────────────────────────────────
def _error(message, status):
    return JSONResponse({"error": message}, status_code=status)


async def _body(request):
    try:
        d = await request.json()
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


@router.get("/api/code/lock")
async def lock_status(request: Request):
    keys = passkeys()
    return {"registered": bool(keys), "unlocked_until": unlocked_until(request) or None,
            "passkeys": [{"id": k["id"][:12], "label": k["label"], "created": k["created"],
                          "last_used": k["last_used"]} for k in keys]}


@router.post("/api/code/passkey/options")
async def register_options(request: Request):
    if passkeys() and not unlocked_until(request):
        return _error("per aggiungere un'altra passkey sblocca prima con quella che hai", 403)
    rp_id = rp_id_of(request)
    return {"challenge": new_challenge("register"), "rp": {"id": rp_id, "name": "Hermes"},
            "user": {"id": b64u(b"hermes-hub-owner"), "name": "hermes", "displayName": "Hermes Hub"},
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
            "authenticatorSelection": {"authenticatorAttachment": "platform", "residentKey": "preferred",
                                       "userVerification": "required"},
            "excludeCredentials": [{"type": "public-key", "id": k["id"]} for k in passkeys()],
            "attestation": "none", "timeout": 60000}


@router.post("/api/code/passkey")
async def register_route(request: Request):
    if passkeys() and not unlocked_until(request):
        return _error("per aggiungere un'altra passkey sblocca prima con quella che hai", 403)
    d = await _body(request)
    try:
        cred = register(rp_id_of(request), unb64u(d.get("clientDataJSON") or ""),
                        unb64u(d.get("attestationObject") or ""), str(d.get("label") or "iPhone"))
    except (Refused, ValueError, KeyError, IndexError) as e:
        audit.record("pwa", "passkey-register", str(e), ok=False)
        return _error(f"registrazione non riuscita: {e}", 400)
    audit.record("pwa", "passkey-register", f"passkey {cred[:12]}")
    exp = time.time() + UNLOCK_S
    response = JSONResponse({"ok": True, "unlocked_until": int(exp)})
    _set_cookie(response, exp)
    return response


@router.post("/api/code/unlock/options")
async def unlock_options(request: Request):
    keys = [k for k in passkeys() if k["rp_id"] == rp_id_of(request)]
    if not keys:
        return _error("nessuna passkey registrata per questo indirizzo", 404)
    return {"challenge": new_challenge("unlock"), "rpId": rp_id_of(request), "userVerification": "required",
            "allowCredentials": [{"type": "public-key", "id": k["id"]} for k in keys], "timeout": 60000}


@router.post("/api/code/unlock")
async def unlock_route(request: Request):
    d = await _body(request)
    try:
        verify(rp_id_of(request), str(d.get("id") or ""), unb64u(d.get("clientDataJSON") or ""),
               unb64u(d.get("authenticatorData") or ""), unb64u(d.get("signature") or ""))
    except (Refused, ValueError) as e:
        audit.record("pwa", "code-unlock", str(e), ok=False)
        return _error(f"sblocco non riuscito: {e}", 403)
    audit.record("pwa", "code-unlock", "Face ID")
    exp = time.time() + UNLOCK_S
    response = JSONResponse({"ok": True, "unlocked_until": int(exp)})
    _set_cookie(response, exp)
    return response


@router.post("/api/code/lock")
async def lock_now():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return response
