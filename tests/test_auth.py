"""Access control: an open tunnel URL means an open agent."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ["HERMES_API_KEY"] = "k-agent"
os.environ["HERMES_DASHBOARD_TOKEN"] = "k-dash"
os.environ["VOICE_AUTH_TOKEN"] = "segreto-di-prova"
import app as srv

c = srv.app.test_client()
TOK = "segreto-di-prova"

# senza credenziali: tutto chiuso
for path in ["/", "/app.js", "/chat", "/tts", "/transcribe", "/voice-config"]:
    r = c.get(path) if path in ("/", "/app.js", "/voice-config") else c.post(path, json={})
    assert r.status_code == 401, f"{path} doveva dare 401, ha dato {r.status_code}"
print("senza token: 401 su /, /app.js, /chat, /tts, /transcribe, /voice-config  OK")

# un browser che apre la pagina deve ricevere un modo per rientrare,
# non JSON grezzo: senza, un telefono bloccato fuori resta senza indicazioni
r = c.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
assert r.status_code == 401
assert "text/html" in r.headers["Content-Type"], r.headers["Content-Type"]
body = r.get_data(as_text=True)
assert "<form" in body and "type=password" in body, body[:200]
assert TOK not in body, "la pagina non deve contenere il token"
print("401 su navigazione: pagina di accesso, senza segreti dentro  OK")

# le chiamate API restano JSON: un client non deve ricevere HTML
r = c.post("/tts", json={}, headers={"Accept": "application/json"})
assert r.status_code == 401 and r.get_json()["error"] == "unauthorized"
print("401 su API: JSON  OK")

# /health resta pubblico per il monitoraggio
assert c.get("/health").status_code == 200
print("/health pubblico  OK")

# token sbagliato: chiuso
assert c.get("/?k=sbagliato").status_code == 401
print("token errato: 401  OK")

# token giusto: passa e rilascia il cookie
r = c.get("/?k=" + TOK)
assert r.status_code == 200, r.status_code
cookie = r.headers.get("Set-Cookie", "")
assert "hv_auth=" in cookie, cookie
assert "HttpOnly" in cookie and "SameSite=Lax" in cookie, cookie
assert TOK not in cookie, "il token non deve finire nel cookie"
print("token valido: 200 + cookie HttpOnly firmato, senza il segreto dentro  OK")

# il cookie da solo basta per le chiamate successive
assert c.get("/").status_code == 200
print("cookie: accesso senza ripassare il token  OK")

# un cookie manomesso non passa
c.set_cookie("hv_auth", "99999999999.deadbeef")
assert c.get("/").status_code == 401
print("cookie con firma non valida: 401  OK")

# scaduto: rifiutato anche con firma buona
c.set_cookie("hv_auth", srv._auth_cookie_value(0))
assert c.get("/").status_code == 401
print("cookie scaduto: 401  OK")

# senza token configurato il server rifiuta tutto invece di aprirsi
srv.VOICE_AUTH_TOKEN = ""
assert c.get("/health").status_code == 503
print("VOICE_AUTH_TOKEN assente: 503, fail closed  OK")

print()
print("OK: il gate regge token mancante, errato, manomesso, scaduto e non configurato")
