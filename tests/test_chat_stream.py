"""A turn on a Hermes session: what the phone receives, spoken and written."""
import json

import _setup
import httpx

NL = chr(10)
RUN = "run_" + "a" * 32


def ev(name, **data):
    return f"event: {name}{NL}data: {json.dumps(data)}{NL}{NL}"


STREAM = "".join([
    ev("run.started", run_id=RUN, session_id="s1"),
    ev("message.started", message={"id": "m1", "role": "assistant"}),
    ev("assistant.delta", delta="Certo. "),
    ev("assistant.delta", delta="Controllo subito"),
    ev("tool.started", tool_name="terminal", preview="echo $((12*3))", args={"command": "echo $((12*3))"}),
    ev("approval.request", run_id=RUN, request_id="req1", command="echo $((12*3))",
       description="Security scan", choices=["once", "session", "deny"]),
    ev("tool.completed", tool_name="terminal"),
    ev("tool.progress", tool_name="_thinking", delta="ragionamento privato"),   # must NOT reach the phone
    ev("assistant.delta", delta="Fa 36. "),
    ev("assistant.delta", delta="Serve altro?"),
    ev("assistant.completed", content="Certo. Controllo subitoFa 36. Serve altro?"),
    ev("run.completed", completed=True),
    ev("done"),
])

seen = []


def hermes_api(req):
    body = json.loads(req.content) if req.content else None
    seen.append((req.method, req.url.path, dict(req.headers), body))
    if req.method == "POST" and req.url.path == "/api/sessions":
        return httpx.Response(201, json={"session": {"id": "api_1_new"}})
    if req.url.path.endswith("/chat/stream"):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=STREAM.encode())
    raise AssertionError(f"unexpected {req.method} {req.url}")


def events(text):
    out = [json.loads(line[5:]) for line in text.split(NL) if line.startswith("data:")]
    return [e for e in out if e["type"] != "flush"]


_setup.mock(hermes_api)
with _setup.signed_in_client() as c:
    # --- voice turn, no session yet ---
    r = c.post("/api/chat", json={"message": "quanto fa 12 per 3?", "voice": True})
    assert r.status_code == 200 and r.headers.get("x-accel-buffering") == "no", r.status_code
    evs = events(r.text)
    for e in evs:
        print("  ", e["type"], {k: v for k, v in e.items() if k not in ("type", "seq")})

    assert evs[0] == {"seq": 0, "type": "session", "session_id": "api_1_new"}, evs[0]
    assert evs[1]["type"] == "run" and evs[1]["run_id"] == RUN
    assert [e["seq"] for e in evs] == sorted(e["seq"] for e in evs), "seq out of order"
    assert "ragionamento" not in r.text, "reasoning reached the phone!"

    sentences = [e["text"] for e in evs if e["type"] == "sentence"]
    assert sentences[0] == "Certo.", sentences
    assert "Controllo subito" in sentences, "text before a tool call was not spoken"
    assert any("conferma" in s for s in sentences), "no spoken cue for the approval"
    assert sentences[-1] == "Fa 36. Serve altro?", sentences

    text = "".join(e["text"] for e in evs if e["type"] == "delta")
    assert text == "Certo. Controllo subitoFa 36. Serve altro?", text

    tools = [(e["name"], e["state"]) for e in evs if e["type"] == "tool"]
    assert tools == [("terminal", "running"), ("terminal", "done")], tools
    appr = next(e for e in evs if e["type"] == "approval")
    assert appr["run_id"] == RUN and appr["request_id"] == "req1" and appr["command"] == "echo $((12*3))"
    assert appr["choices"] == ["once", "session", "deny"]
    assert evs[-1]["type"] == "done" and evs[-1]["status"] == "completed", evs[-1]
    print("  voce: sessione creata, frasi, strumenti, approvazione, fine  OK")

    create = next(s for s in seen if s[1] == "/api/sessions")
    method, path, headers, body = next(s for s in seen if s[1].endswith("/chat/stream"))
    assert path == "/api/sessions/api_1_new/chat/stream", path
    assert headers["authorization"] == "Bearer k-agent"
    assert headers["x-hermes-session-key"] == "hermes-voice:pwa"
    assert body["message"] == "quanto fa 12 per 3?"
    assert "italiano" in body["system_message"], "voice prompt missing on a voice turn"
    print("  bearer, scope della memoria, prompt vocale  OK")

    # --- typed turn in the same session: no speech, no voice prompt ---
    seen.clear()
    r = c.post("/api/chat", json={"message": "e 12 per 4?", "session_id": "api_1_new"})
    evs = events(r.text)
    assert not any(e["type"] == "sentence" for e in evs), "a typed turn produced speech"
    assert not any(s[1] == "/api/sessions" for s in seen), "created a session that already existed"
    body = next(s for s in seen if s[1].endswith("/chat/stream"))[3]
    assert "system_message" not in body, body
    print("  testo: stessa sessione, niente voce, niente prompt vocale  OK")

    # --- bad input never reaches Hermes ---
    seen.clear()
    assert c.post("/api/chat", json={"message": "  "}).status_code == 400
    assert c.post("/api/chat", json={"message": "x", "session_id": "../../v1/runs"}).status_code == 400
    assert not seen, seen
    print("  messaggio vuoto e id di sessione malformato: 400 senza chiamare Hermes  OK")

# --- Hermes errors read as what they are ---
_setup.mock(lambda req: httpx.Response(401, text='{"error":"unauthorized"}'))
with _setup.signed_in_client() as c:
    r = c.post("/api/chat", json={"message": "x", "session_id": "s1"})
    assert r.status_code == 502 and "HERMES_API_KEY" in r.json()["error"], r.text
print("  401 da Hermes -> messaggio sulla chiave  OK")

_setup.mock(lambda req: httpx.Response(404, json={"error": {"message": "Session not found: s9"}}))
with _setup.signed_in_client() as c:
    r = c.post("/api/chat", json={"message": "x", "session_id": "s9"})
    assert r.status_code == 404 and "Session not found" in r.json()["error"], r.text
print("  sessione inesistente -> 404  OK")

print()
print("OK: turno vocale e scritto sulla stessa sessione, frasi in streaming, approvazione, errori leggibili")
