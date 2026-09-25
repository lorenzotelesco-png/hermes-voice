"""The access gate: an open tunnel URL means an open agent."""
import time

import _setup
import httpx
from hub import audit, config, security

_setup.mock(lambda req: httpx.Response(200, json={"ok": True}))
TOK = _setup.TOKEN

# no credentials: everything closed
c = _setup.anon_client()
for path in ["/", "/api/voice-config"]:
    assert c.get(path).status_code == 401, path
for path in ["/api/chat", "/api/tts", "/api/transcribe"]:
    assert c.post(path, json={}).status_code == 401, path
print("senza token: 401 su /, /api/chat, /api/tts, /api/transcribe, /api/voice-config  OK")

# a browser opening the page gets a way back in, not raw JSON
r = c.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
assert r.status_code == 401 and "text/html" in r.headers["content-type"]
assert "<form" in r.text and "type=password" in r.text, r.text[:200]
assert TOK not in r.text, "the login page must not contain the token"
print("401 su navigazione: pagina di accesso, senza segreti dentro  OK")

# API calls stay JSON
r = c.post("/api/tts", json={}, headers={"Accept": "application/json"})
assert r.status_code == 401 and r.json()["error"] == "unauthorized"
print("401 su API: JSON  OK")

# health stays public for monitoring, at both paths
assert c.get("/health").status_code == 200 and c.get("/api/health").status_code == 200
print("/health e /api/health pubblici  OK")

# wrong token
assert c.get("/?k=sbagliato").status_code == 401
print("token errato: 401  OK")

# right token: through, and the cookie is issued
r = c.get("/api/voice-config?k=" + TOK)
assert r.status_code == 200, r.status_code
cookie = r.headers.get("set-cookie", "")
assert security.COOKIE + "=" in cookie, cookie
assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower(), cookie
assert TOK not in cookie, "the token must not end up in the cookie"
print("token valido: 200 + cookie HttpOnly firmato, senza il segreto dentro  OK")

# the cookie alone is enough afterwards
assert c.get("/api/voice-config").status_code == 200
print("cookie: accesso senza ripassare il token  OK")

# a cookie signed exactly as the Flask app signed it is still valid: the phone
# already holds one, and must not be logged out by the migration
import hashlib, hmac  # noqa: E401,E402
raw = str(int(time.time() + 3600))
legacy = raw + "." + hmac.new(TOK.encode(), raw.encode(), hashlib.sha256).hexdigest()
c2 = _setup.anon_client()
c2.cookies.set("hv_auth", legacy)
assert c2.get("/api/voice-config").status_code == 200
print("cookie emesso dalla vecchia app: ancora valido  OK")

# tampered and expired cookies are refused
c3 = _setup.anon_client()
c3.cookies.set(security.COOKIE, "99999999999.deadbeef")
assert c3.get("/api/voice-config").status_code == 401
c3.cookies.set(security.COOKIE, security.cookie_value(0))
assert c3.get("/api/voice-config").status_code == 401
print("cookie manomesso o scaduto: 401  OK")

# cross-site writes: a signed-in phone visiting another site must not be
# usable to post here
me = _setup.signed_in_client()
r = me.post("/api/tts", json={"text": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
assert r.status_code == 403, r.status_code
r = me.post("/api/tts", json={"text": "x"}, headers={"Origin": "https://evil.example"})
assert r.status_code == 403, r.status_code
r = me.post("/api/tts", json={"text": ""}, headers={"Origin": "http://testserver",
                                                    "Sec-Fetch-Site": "same-origin"})
assert r.status_code == 400, r.status_code          # past the gate, into the handler
print("POST cross-site: 403; same-origin: passa  OK")

# unknown API paths are a 404, not index.html with a 200
assert me.get("/api/nope").status_code == 404
print("API sconosciuta: 404  OK")

# refusals are recorded
assert any(e["action"] == "denied" for e in audit.recent()), audit.recent()
print("rifiuti registrati nell'audit log  OK")

# no token configured: refuse everything instead of opening up
config.VOICE_AUTH_TOKEN = ""
assert me.get("/api/voice-config").status_code == 503
assert me.get("/health").status_code == 503
print("VOICE_AUTH_TOKEN assente: 503, fail closed  OK")

print()
print("OK: il gate regge token mancante, errato, manomesso, scaduto, cross-site e non configurato")
