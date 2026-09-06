import io, os, sys, json, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
os.environ["HERMES_API_KEY"] = "test-key"
os.environ["HERMES_DASHBOARD_TOKEN"] = "test-dash"

import urllib.request
import app as srv

NL = chr(10)

class FakeUpstream:
    """Finge la risposta SSE di Hermes: delta a pezzi + un evento tool-progress."""
    def __init__(self, lines): self._lines = lines; self.closed = False
    def __iter__(self):
        for l in self._lines: yield (l + NL).encode()
    def close(self): self.closed = True

def make_stream():
    def d(txt):
        return "data: " + json.dumps({"choices":[{"delta":{"content":txt}}]})
    return [
        "event: hermes.tool.progress",
        'data: {"tool":"web_search"}',       # NON deve finire nel parlato
        "",
        d("Certo. "), "",
        d("Il meteo a Milano "), "",
        d("oggi e sereno. "), "",
        d("Massima 22 gradi."), "",
        "data: [DONE]", "",
    ]

srv.urllib.request.urlopen = lambda req, timeout=None: FakeUpstream(make_stream())

c = srv.app.test_client()
r = c.post("/chat", json={"history":[{"role":"user","content":"che tempo fa?"}],
                          "session_id":"t1"})
print("status:", r.status_code, "| content-type:", r.headers.get("Content-Type"))
print("X-Accel-Buffering:", r.headers.get("X-Accel-Buffering"))
body = r.get_data(as_text=True)
events = [json.loads(l[5:].strip()) for l in body.split(NL) if l.startswith("data:")]
print()
for e in events:
    if "sentence" in e: print("  FRASE:", repr(e["sentence"]))
    elif e.get("done"): print("  DONE  :", repr(e["reply"]))
    else: print("  ALTRO :", e)

assert not any("web_search" in json.dumps(e) for e in events), "tool progress e finito nello stream!"
assert sum(1 for e in events if "sentence" in e) >= 2, "frasi non emesse"
assert events[-1].get("done") is True, "manca l'evento done"
print()
print("OK: tool-progress filtrato, frasi emesse in streaming, done presente")
