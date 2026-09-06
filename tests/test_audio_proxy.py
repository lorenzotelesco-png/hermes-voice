import io, os, sys, json, base64
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ["HERMES_API_KEY"]="k-agent"; os.environ["HERMES_DASHBOARD_TOKEN"]="k-dash"; os.environ["VOICE_AUTH_TOKEN"]="test-auth"
import urllib.request, urllib.error
import app as srv

seen = {}
class Resp:
    def __init__(self, payload): self._p=json.dumps(payload).encode()
    def read(self): return self._p
    def __enter__(self): return self
    def __exit__(self,*a): return False

def fake_urlopen(req, timeout=None):
    seen['url']=req.full_url; seen['headers']=dict(req.header_items())
    if '/api/audio/transcribe' in req.full_url:
        return Resp({"ok":True,"transcript":"ciao hermes","provider":"deepinfra"})
    if '/api/audio/speak' in req.full_url:
        return Resp({"ok":True,"data_url":"data:audio/wav;base64,"+base64.b64encode(b'RIFFfake').decode(),
                     "mime_type":"audio/wav","provider":"piper"})
    raise AssertionError('url inatteso '+req.full_url)
srv.urllib.request.urlopen = fake_urlopen
c = srv.app.test_client()

# --- /transcribe ---
r = c.post("/transcribe?k=test-auth", data={"audio":(io.BytesIO(b"\x00\x01audio"),"s.webm","audio/webm")},
           content_type="multipart/form-data")
print("transcribe:", r.status_code, r.get_json())
assert r.get_json()["text"]=="ciao hermes"
assert seen['headers'].get('X-hermes-session-token')=='k-dash', seen['headers']
print("  header auth dashboard OK")

# --- /tts ---
r = c.post("/tts?k=test-auth", json={"text":"prova"})
j=r.get_json(); print("tts:", r.status_code, {k:(v[:16]+'...' if k=='audio' else v) for k,v in j.items()})
assert base64.b64decode(j["audio"])==b'RIFFfake' and j["mime"]=="audio/wav"
print("  base64 estratto dal data_url OK")

# --- dashboard 401 -> messaggio azionabile ---
def fake_401(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"Unauthorized"}'))
srv.urllib.request.urlopen = fake_401
r = c.post("/tts?k=test-auth", json={"text":"x"})
print("tts 401:", r.status_code, "->", r.get_json()["error"][:70]+"...")
assert r.status_code==502 and "HERMES_DASHBOARD_SESSION_TOKEN" in r.get_json()["error"]

# --- dashboard giu -> 503 ---
def fake_down(req, timeout=None):
    raise urllib.error.URLError("Connection refused")
srv.urllib.request.urlopen = fake_down
r = c.post("/tts?k=test-auth", json={"text":"x"})
print("tts down:", r.status_code, "->", r.get_json()["error"][:60]+"...")
assert r.status_code==503
print()
print("OK: proxy transcribe/tts, auth header, 401 e 503 distinti")
