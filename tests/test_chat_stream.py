"""SSE framing, incremental sentence cutting, and tool progress never reaching the speaker."""
import json

import _setup
import httpx

NL = chr(10)


def d(txt):
    return "data: " + json.dumps({"choices": [{"delta": {"content": txt}}]})


STREAM = [
    "event: hermes.tool.progress",
    'data: {"tool":"web_search"}',       # must NOT end up spoken
    "",
    d("Certo. "), "",
    d("Il meteo a Milano "), "",
    d("oggi e sereno. "), "",
    d("Massima 22 gradi."), "",
    "data: [DONE]", "",
]

seen = {}


def hermes_api(req):
    seen["url"] = str(req.url)
    seen["auth"] = req.headers.get("authorization")
    seen["session"] = req.headers.get("x-hermes-session-id")
    seen["body"] = json.loads(req.content)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=(NL.join(STREAM) + NL).encode())


_setup.mock(hermes_api)
c = _setup.signed_in_client()
r = c.post("/api/chat", json={"history": [{"role": "user", "content": "che tempo fa?"}],
                              "session_id": "t1"})
print("status:", r.status_code, "| content-type:", r.headers.get("content-type"))
assert r.status_code == 200
assert r.headers.get("x-accel-buffering") == "no"

events = [json.loads(l[5:].strip()) for l in r.text.split(NL) if l.startswith("data:")]
for e in events:
    if "sentence" in e:
        print("  FRASE:", repr(e["sentence"]))
    elif e.get("done"):
        print("  DONE  :", repr(e["reply"]))
    else:
        print("  ALTRO :", e)

assert not any("web_search" in json.dumps(e) for e in events), "tool progress reached the stream!"
assert sum(1 for e in events if "sentence" in e) >= 2, "sentences not emitted"
assert events[-1].get("done") is True, "missing done event"

assert seen["auth"] == "Bearer k-agent", seen["auth"]
assert seen["session"] == "t1", seen["session"]
assert seen["body"]["stream"] is True
assert seen["body"]["messages"][0]["role"] == "system", "voice system prompt missing"
assert seen["body"]["messages"][-1]["content"] == "che tempo fa?"
print("  bearer, session header, system prompt  OK")

# Hermes rejecting the key must read as a key problem, not a generic failure.
_setup.mock(lambda req: httpx.Response(401, text='{"error":"unauthorized"}'))
r = c.post("/api/chat", json={"history": [{"role": "user", "content": "x"}]})
assert r.status_code == 502 and "HERMES_API_KEY" in r.json()["error"], r.text
print("  401 da Hermes -> messaggio sulla chiave  OK")

print()
print("OK: tool-progress filtrato, frasi in streaming, done presente, errori leggibili")
