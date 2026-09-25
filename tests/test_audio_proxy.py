"""The dashboard proxy: auth header, data-URL handling, 401 vs 503, voice-config."""
import base64

import _setup
from _setup import anon_client
import httpx

seen = {}


def dashboard(req):
    seen["url"] = str(req.url)
    seen["token"] = req.headers.get("x-hermes-session-token")
    if req.url.path == "/api/audio/transcribe":
        return httpx.Response(200, json={"ok": True, "transcript": "ciao hermes", "provider": "deepinfra"})
    if req.url.path == "/api/audio/speak":
        return httpx.Response(200, json={
            "ok": True, "mime_type": "audio/wav", "provider": "edge",
            "data_url": "data:audio/wav;base64," + base64.b64encode(b"RIFFfake").decode()})
    raise AssertionError("unexpected url " + str(req.url))


_setup.mock(dashboard)
c = _setup.signed_in_client()

# --- transcribe ---
r = c.post("/api/transcribe", files={"audio": ("s.webm", b"\x00\x01audio", "audio/webm")})
print("transcribe:", r.status_code, r.json())
assert r.json()["text"] == "ciao hermes"
assert seen["token"] == "k-dash", seen
print("  header auth dashboard OK")

r = c.post("/api/transcribe")
assert r.status_code == 400, r.status_code
print("  senza audio: 400 OK")

# --- tts ---
r = c.post("/api/tts", json={"text": "prova"})
j = r.json()
assert base64.b64decode(j["audio"]) == b"RIFFfake" and j["mime"] == "audio/wav", j
print("tts: base64 estratto dal data_url OK")

# --- dashboard 401 -> actionable message ---
_setup.mock(lambda req: httpx.Response(401, json={"detail": "Unauthorized"}))
r = c.post("/api/tts", json={"text": "x"})
print("tts 401:", r.status_code, "->", r.json()["error"][:70] + "...")
assert r.status_code == 502 and "HERMES_DASHBOARD_SESSION_TOKEN" in r.json()["error"]


# --- dashboard down -> 503 ---
def down(req):
    raise httpx.ConnectError("Connection refused", request=req)


_setup.mock(down)
r = c.post("/api/tts", json={"text": "x"})
print("tts down:", r.status_code, "->", r.json()["error"][:60] + "...")
assert r.status_code == 503

# --- voice-config passes direct mode through ---
_setup.mock(lambda req: httpx.Response(200, json={"ok": True, "stt": {
    "mode": "direct", "wire": "openai-multipart", "provider": "deepinfra", "api_key": "k-di",
    "base_url": "https://api.deepinfra.com/v1/openai",
    "model": "openai/whisper-large-v3-turbo", "language": "it"}}))
j = c.get("/api/voice-config").json()
print("voice-config:", {k: v for k, v in j["stt"].items() if k != "api_key"})
assert j["stt"]["mode"] == "direct" and j["stt"]["api_key"] == "k-di"

# dashboard down: degrade to relay, never break the session
_setup.mock(down)
j = c.get("/api/voice-config").json()
print("voice-config con dashboard giu':", j["stt"]["mode"])
assert j["stt"]["mode"] == "relay", j

# --- diagnostics from the phone: into the journal, capped ---
import contextlib, io  # noqa: E402
out = io.StringIO()
with contextlib.redirect_stdout(out):
    r = c.post("/api/diag", json={"event": "calibrated", "data": {"floor": 3.1, "peak": 0}})
    assert r.json() == {"ok": True}, r.text
    c.post("/api/diag", json={"event": "x" * 500, "data": {"blob": "y" * 5000}})
    oks = [c.post("/api/diag", json={"event": "flood"}).json()["ok"] for _ in range(80)]
lines = out.getvalue().splitlines()
assert '[CLIENT] calibrated {"floor": 3.1, "peak": 0}' in lines, lines[:2]
assert all(len(l) < 700 for l in lines), "a diagnostic line was not capped"
assert oks.count(True) == 58 and not oks[-1], oks.count(True)
assert anon_client().post("/api/diag", json={"event": "x"}).status_code == 401
print("diagnostica: nel journal, righe limitate, massimo 60 al minuto, solo con accesso  OK")

print()
print("OK: proxy transcribe/tts, auth header, 401 e 503 distinti, voice-config")
