"""The Code tab: Face ID (a software passkey signing like an iPhone), then OpenCode behind it."""
import asyncio
import base64
import hashlib
import json
import os

import _setup
import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from hub import audit, opencode, passkey

RP = "testserver"
ORIGIN = f"https://{RP}"


# ── a passkey in software ──────────────────────────────────────────
def cbor(v):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Phone:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred = os.urandom(16)
        n = self.key.public_key().public_numbers()
        self.x, self.y = n.x.to_bytes(32, "big"), n.y.to_bytes(32, "big")

    def auth_data(self, flags=0x05, count=0, attested=False, rp=RP):
        ad = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")
        if attested:
            ad += b"\0" * 16 + len(self.cred).to_bytes(2, "big") + self.cred
            ad += cbor({1: 2, 3: -7, -1: 1, -2: self.x, -3: self.y})
        return ad

    def create(self, challenge, origin=ORIGIN, flags=0x45):
        cd = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": origin}).encode()
        att = cbor({"fmt": "none", "attStmt": {}, "authData": self.auth_data(flags, attested=True)})
        return {"clientDataJSON": passkey.b64u(cd), "attestationObject": passkey.b64u(att)}

    def get(self, challenge, origin=ORIGIN, flags=0x05, rp=RP, tamper=False, count=0):
        cd = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin}).encode()
        ad = self.auth_data(flags, count=count, rp=rp)
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        if tamper:
            ad = ad[:-1] + bytes([ad[-1] ^ 1])
        return {"id": passkey.b64u(self.cred), "clientDataJSON": passkey.b64u(cd),
                "authenticatorData": passkey.b64u(ad), "signature": passkey.b64u(sig)}


# ── OpenCode, stubbed ──────────────────────────────────────────────
calls = []
NL = chr(10)


def oc_event(sid, type_, **props):
    return "data: " + json.dumps({"directory": "/root", "payload": {"type": type_, "properties": {"sessionID": sid, **props}}}) + NL + NL


def fake_opencode(req):
    p, q = req.url.path, dict(req.url.params)
    calls.append((req.method, p, q, json.loads(req.content) if req.content else None))
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"opencode:oc-pass").decode()
    if p == "/experimental/session":
        return httpx.Response(200, json=[
            {"id": "ses_a", "title": "Vecchia", "directory": "/root", "time": {"created": 1000, "updated": 2000}},
            {"id": "ses_b", "title": "Hub", "directory": "/opt/hermes-hub", "time": {"created": 3000, "updated": 9000},
             "summary": {"additions": 4, "deletions": 1, "files": 2}}])
    if p == "/project":
        return httpx.Response(200, json=[{"id": "global", "worktree": "/"}, {"id": "p1", "worktree": "/opt/nume"}])
    if p == "/session" and req.method == "POST":
        return httpx.Response(200, json={"id": "ses_new", "title": "", "directory": q["directory"], "time": {}})
    if p == "/session/ses_b":
        return httpx.Response(200, json={"id": "ses_b", "title": "Hub", "directory": "/opt/hermes-hub", "time": {}})
    if p == "/session/ses_b/message":
        return httpx.Response(200, json=[
            {"info": {"id": "m1", "role": "user"}, "parts": [{"id": "p1", "type": "text", "text": "conta i file"}]},
            {"info": {"id": "m2", "role": "assistant", "modelID": "muse"}, "parts": [
                {"id": "p2", "type": "step-start"},
                {"id": "p3", "type": "reasoning", "text": "Uso ls."},
                {"id": "p4", "type": "tool", "tool": "bash", "state": {"status": "completed", "title": "ls",
                                                                      "input": {"command": "ls | wc -l"}, "output": "x" * 5000}},
                {"id": "p5", "type": "text", "text": "Sono 12 file."},
                {"id": "p6", "type": "step-finish", "cost": 0}]}])
    if p == "/session/status":
        return httpx.Response(200, json={"ses_b": {"type": "busy"}})
    if p == "/permission":
        return httpx.Response(200, json=[
            {"id": "per_1", "sessionID": "ses_b", "permission": "bash", "patterns": ["rm *"],
             "metadata": {"command": "rm -rf build"}, "always": []},
            {"id": "per_2", "sessionID": "ses_other", "permission": "edit", "patterns": [], "metadata": {}, "always": []}])
    if p.endswith("/prompt_async") or p.endswith("/abort"):
        return httpx.Response(204)
    if "/permissions/" in p:
        return httpx.Response(200, json=True)
    if p == "/global/event":
        body = (oc_event("ses_b", "session.status", status={"type": "busy"})
                + oc_event("ses_other", "message.part.delta", messageID="m", partID="p", field="text", delta="altro")
                + oc_event("ses_b", "message.part.delta", messageID="m3", partID="p7", field="text", delta="Fat")
                + oc_event("ses_b", "message.part.updated", part={"id": "p8", "messageID": "m3", "sessionID": "ses_b",
                                                                  "type": "tool", "tool": "bash",
                                                                  "state": {"status": "running", "input": {"command": "make"}}})
                + oc_event("ses_b", "permission.asked", id="per_3")
                + oc_event("ses_b", "session.idle"))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
    if p == "/pty" and req.method == "GET":
        return httpx.Response(200, json=[{"id": "pty_1", "title": "Terminale · root", "command": "bash", "cwd": "/root",
                                          "status": "running", "pid": 1}])
    if p == "/pty" and req.method == "POST":
        body = json.loads(req.content)
        return httpx.Response(200, json={"id": "pty_2", "title": body["title"], "command": body["command"],
                                         "cwd": body["cwd"], "status": "running", "pid": 2})
    if p.startswith("/pty/pty_"):
        return httpx.Response(200, json={"id": "pty_1"} if req.method == "PUT" else True)
    raise AssertionError(f"unexpected {req.method} {req.url}")


opencode.transport = httpx.MockTransport(fake_opencode)
opencode.config.OPENCODE_PASSWORD = "oc-pass"
phone = Phone()

with _setup.signed_in_client() as c:
    # ── locked by default ──────────────────────────────────────────
    r = c.get("/api/code/sessions")
    assert r.status_code == 401 and "Face ID" in r.json()["error"], r.text
    assert not calls
    assert c.get("/api/code/lock").json()["registered"] is False
    print("tab Codice chiusa finché non c'è Face ID, OpenCode non viene chiamato  OK")

    # ── first passkey ──────────────────────────────────────────────
    opts = c.post("/api/code/passkey/options").json()
    assert opts["rp"]["id"] == RP and opts["authenticatorSelection"]["userVerification"] == "required"
    bad = phone.create(opts["challenge"], origin="https://evil.example")
    assert c.post("/api/code/passkey", json=bad).status_code == 400, "wrong origin accepted"
    opts = c.post("/api/code/passkey/options").json()
    assert c.post("/api/code/passkey", json=phone.create(opts["challenge"], flags=0x41)).status_code == 400, "no UV accepted"
    opts = c.post("/api/code/passkey/options").json()
    r = c.post("/api/code/passkey", json=phone.create(opts["challenge"]))
    assert r.status_code == 200 and r.cookies.get("hv_code"), r.text
    assert c.get("/api/code/lock").json()["registered"] is True
    assert c.get("/api/code/sessions").status_code == 200
    print("prima passkey: origine sbagliata e niente Face ID rifiutati; registrata, tab sbloccata  OK")

with _setup.signed_in_client() as other:
    # Holding the app's token is not enough to enrol a second face.
    assert other.post("/api/code/passkey/options").status_code == 403
    assert other.post("/api/code/passkey", json=Phone().create("x")).status_code == 403
    print("seconda passkey senza sblocco: rifiutata (un token rubato non aggiunge una faccia)  OK")

    # ── unlocking ──────────────────────────────────────────────────
    def unlock(**kw):
        ch = other.post("/api/code/unlock/options").json()["challenge"]
        return other.post("/api/code/unlock", json=phone.get(ch, **kw))

    assert unlock(tamper=True).status_code == 403
    assert unlock(flags=0x01).status_code == 403
    assert unlock(origin="https://evil.example").status_code == 403
    assert unlock(rp="evil.example").status_code == 403
    stranger = Phone()
    ch = other.post("/api/code/unlock/options").json()["challenge"]
    assert other.post("/api/code/unlock", json=stranger.get(ch)).status_code == 403
    ch = other.post("/api/code/unlock/options").json()["challenge"]
    good = phone.get(ch)
    assert other.post("/api/code/unlock", json=good).status_code == 200
    assert other.post("/api/code/unlock", json=good).status_code == 403, "a replayed unlock was accepted"
    assert other.get("/api/code/sessions").status_code == 200
    print("sblocco: firma alterata, niente Face ID, altro sito, passkey sconosciuta e replay rifiutati  OK")

    other.cookies.set("hv_code", "9999999999.falso", domain="testserver.local", path="/api/code")
    other.post("/api/code/lock")
    assert other.get("/api/code/sessions").status_code == 401
    print("\"blocca\" e cookie falsificati: di nuovo chiusa  OK")

with _setup.signed_in_client() as c:
    ch = c.post("/api/code/unlock/options").json()["challenge"]
    assert c.post("/api/code/unlock", json=phone.get(ch)).status_code == 200

    # ── sessions ───────────────────────────────────────────────────
    s = c.get("/api/code/sessions").json()["sessions"]
    assert [x["id"] for x in s] == ["ses_b", "ses_a"] and s[0]["changes"] == {"additions": 4, "deletions": 1, "files": 2}
    places = [p["dir"] for p in c.get("/api/code/places").json()["places"]]
    assert places == ["/opt/hermes-hub", "/root", "/opt/nume"], places
    d = c.get("/api/code/sessions/ses_b?dir=/opt/hermes-hub").json()
    assert d["status"] == "busy" and [p["id"] for p in d["permissions"]] == ["per_1"], d
    assert d["permissions"][0]["command"] == "rm -rf build"
    parts = d["messages"][1]["parts"]
    assert [p["type"] for p in parts] == ["reasoning", "tool", "text"], parts
    assert parts[1]["input"] == "ls | wc -l" and len(parts[1]["output"]) == 3000 and parts[1]["cut"]
    print("sessioni di tutte le cartelle, trascrizione con ragionamento, strumenti e output tagliato  OK")

    r = c.post("/api/code/sessions", json={"dir": "/opt/hermes-hub"})
    assert r.json()["id"] == "ses_new" and calls[-1][2] == {"directory": "/opt/hermes-hub"}
    assert c.post("/api/code/sessions/ses_b/prompt", json={"dir": "/opt/hermes-hub", "text": "fai i test"}).json() == {"ok": True}
    assert calls[-1][1] == "/session/ses_b/prompt_async" and calls[-1][3] == {"parts": [{"type": "text", "text": "fai i test"}]}
    assert c.post("/api/code/sessions/ses_b/abort", json={"dir": "/opt/hermes-hub"}).json() == {"ok": True}
    assert c.post("/api/code/sessions/ses_b/permissions/per_1", json={"dir": "/opt/hermes-hub", "response": "once"}).json()["ok"]
    assert calls[-1][3] == {"response": "once"}
    n = len(calls)
    assert c.post("/api/code/sessions/ses_b/permissions/per_1", json={"dir": "/root", "response": "always"}).status_code == 400
    assert c.post("/api/code/sessions", json={"dir": "relativa"}).status_code == 400
    assert c.post("/api/code/sessions", json={"dir": "/root/../etc"}).status_code == 400
    assert c.get("/api/code/sessions/..%2Fx?dir=/root").status_code in (400, 404)
    assert c.post("/api/code/sessions/ses_b/prompt", json={"dir": "/root", "text": "x"},
                  headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert len(calls) == n, "a refused request reached OpenCode"
    assert any(x["action"] == "code-prompt" for x in audit.recent(20))
    print("nuova sessione, messaggio, stop, permesso una volta; \"sempre\", cartelle strane e altri siti fermati  OK")

    # ── live events, one session only ──────────────────────────────
    text = c.get("/api/code/sessions/ses_b/events").text
    evs = [json.loads(l[5:]) for l in text.split(NL) if l.startswith("data:")]
    evs = [e for e in evs if e.get("type") != "flush"]
    kinds = [e["t"] for e in evs]
    assert kinds == ["hello", "status", "delta", "part", "permissions", "status"], kinds
    assert evs[2]["delta"] == "Fat" and evs[3]["part"]["input"] == "make" and evs[-1]["status"] == "idle"
    print("eventi dal flusso globale, solo quelli della sessione aperta  OK")

    # ── terminals ──────────────────────────────────────────────────
    assert c.get("/api/code/pty").json()["terminals"][0]["running"]
    t = c.post("/api/code/pty", json={"kind": "opencode", "dir": "/opt/hermes-hub"}).json()
    body = calls[-1][3]
    assert body["command"] == "bash" and body["args"] == ["-lc", "opencode; exec bash -l"], body
    assert body["cwd"] == "/opt/hermes-hub" and body["env"]["TERM"] == "xterm-256color"
    assert t["title"] == "OpenCode · hermes-hub"
    assert c.post("/api/code/pty", json={"kind": "python"}).status_code == 400
    assert c.put("/api/code/pty/pty_1/size", json={"rows": 40, "cols": 60}).json() == {"ok": True}
    assert calls[-1][3] == {"size": {"rows": 40, "cols": 60}}
    assert c.put("/api/code/pty/pty_1/size", json={"rows": 0, "cols": 60}).status_code == 400
    assert c.delete("/api/code/pty/pty_1").json() == {"ok": True}
    print("terminali: shell o OpenCode nella cartella scelta, dimensioni, chiusura  OK")

    # ── the terminal's WebSocket ───────────────────────────────────
    class Upstream:
        def __init__(self):
            self.sent, self.closed = [], False

        def __aiter__(self):
            return self.frames()

        async def frames(self):
            yield "root@v67677:~# "
            yield b"\x00" + json.dumps({"cursor": 15}).encode()
            await asyncio.sleep(0.5)

        async def send(self, data):
            self.sent.append(data)

        async def close(self):
            self.closed = True

    up, opened = Upstream(), []

    async def fake_connect(url, headers):
        opened.append((url, headers))
        return up

    opencode.ws_connect = fake_connect
    # The test client speaks ws://, where a browser would not send the Secure
    # unlock cookie either; on the phone it is wss://. Hand the cookies over.
    jar = lambda cl: "; ".join(f"{k}={v}" for k, v in cl.cookies.items())  # noqa: E731
    with c.websocket_connect("/api/code/pty/pty_1/ws?cursor=3", headers={"origin": ORIGIN, "cookie": jar(c)}) as ws:
        assert ws.receive_text() == "root@v67677:~# "
        assert ws.receive_bytes().startswith(b"\x00")
        ws.send_text("ls\r")
        import time
        time.sleep(0.2)
    assert up.sent == ["ls\r"] and opened[0][0] == "ws://127.0.0.1:4096/pty/pty_1/connect?cursor=3", opened
    assert opened[0][1]["Authorization"].startswith("Basic ")
    print("terminale: websocket inoltrato nei due sensi, ripresa dal cursore, password mai al telefono  OK")


def ws_refused(client, headers):
    try:
        with client.websocket_connect("/api/code/pty/pty_1/ws", headers=headers) as ws:
            ws.receive_text()
    except Exception as e:  # noqa: BLE001 — starlette raises WebSocketDisconnect with the close code
        return getattr(e, "code", None)
    return None


with _setup.anon_client() as anon:
    assert ws_refused(anon, {"origin": ORIGIN}) == 4401
with _setup.signed_in_client() as locked:
    assert ws_refused(locked, {"origin": ORIGIN, "cookie": jar(locked)}) == 4403
with _setup.signed_in_client() as c:
    ch = c.post("/api/code/unlock/options").json()["challenge"]
    c.post("/api/code/unlock", json=phone.get(ch))
    assert ws_refused(c, {"origin": "https://evil.example", "cookie": jar(c)}) == 4403
    assert ws_refused(c, {"cookie": jar(c)}) == 4403
print("websocket: senza login 4401, senza Face ID 4403, da un altro sito 4403  OK")

print()
print("OK: tab Codice")
