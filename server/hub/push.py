"""Push notifications to the phone: who is subscribed, and sending to them.

They travel through Apple's (or Google's) push service, not the tunnel, so an
alert that ngrok is down still arrives.
"""
import json
import os
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from . import config, hermes, webpush

_lock = threading.Lock()
_ready = set()
_key = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY,
    p256dh   TEXT NOT NULL,
    auth     TEXT NOT NULL,
    subject  TEXT NOT NULL,
    created  REAL NOT NULL,
    last_ok  REAL
)"""

# The push services browsers actually use. Anything else is refused: an
# endpoint is a URL the server will POST to, and it must not be pointable
# at arbitrary hosts.
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")


def _connect():
    conn = sqlite3.connect(config.HUB_DB)
    if config.HUB_DB not in _ready:
        conn.execute(_SCHEMA)
        _ready.add(config.HUB_DB)
    return conn


def _key_path():
    return os.path.join(os.path.dirname(os.path.abspath(config.HUB_DB)), "vapid.pem")


def key():
    global _key
    if _key is None:
        _key = webpush.load_or_create_key(_key_path())
    return _key


def public_key():
    return webpush.b64u(webpush.public_bytes(key()))


def valid_endpoint(url):
    parts = urlsplit(url or "")
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def subscribe(sub, subject):
    """`subject` is the app's own https origin: VAPID wants a contact, and this
    one identifies the sender without handing Apple an email address."""
    keys = sub.get("keys") or {}
    if not valid_endpoint(sub.get("endpoint")) or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("invalid subscription")
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO push_subs (endpoint, p256dh, auth, subject, created) VALUES (?, ?, ?, ?, ?)",
            (sub["endpoint"], keys["p256dh"], keys["auth"], subject, time.time()))


def unsubscribe(endpoint):
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))


def subscriptions():
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT endpoint, p256dh, auth, subject FROM push_subs").fetchall()
    return [dict(zip(("endpoint", "p256dh", "auth", "subject"), r)) for r in rows]


async def send_all(title, body, tag="hub", url="/#/server"):
    """Send to every subscribed device. Returns how many got it."""
    payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url}, ensure_ascii=False).encode()
    sent = 0
    async with hermes.client(15) as c:
        for s in subscriptions():
            headers = {
                **webpush.vapid_headers(s["endpoint"], key(), s["subject"]),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                # A server alert is stale after an hour; high urgency so a
                # phone in low-power mode still wakes for it.
                "TTL": "3600",
                "Urgency": "high",
            }
            try:
                r = await c.post(s["endpoint"], content=webpush.encrypt(payload, s["p256dh"], s["auth"]),
                                 headers=headers)
            except Exception as e:  # noqa: BLE001 — one device failing must not stop the rest
                print(f"[PUSH] {urlsplit(s['endpoint']).hostname}: {e}")
                continue
            if r.status_code in (404, 410):
                # The browser dropped the subscription (app removed, permission revoked).
                unsubscribe(s["endpoint"])
                print(f"[PUSH] subscription gone ({r.status_code}), removed")
            elif r.status_code >= 400:
                print(f"[PUSH] {urlsplit(s['endpoint']).hostname}: HTTP {r.status_code} {r.text[:200]}")
            else:
                sent += 1
                with _lock, _connect() as conn:
                    conn.execute("UPDATE push_subs SET last_ok = ? WHERE endpoint = ?", (time.time(), s["endpoint"]))
    return sent
