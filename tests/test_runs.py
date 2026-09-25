"""Approvals and stop from the phone, and a turn picked up again after the connection drops."""
import json

import _setup
import httpx
from hub import audit

NL = chr(10)
RUN = "run_" + "b" * 32


def ev(name, **data):
    return f"event: {name}{NL}data: {json.dumps(data)}{NL}{NL}"


STREAM = "".join([
    ev("run.started", run_id=RUN),
    ev("assistant.delta", delta="Eseguo."),
    ev("approval.request", run_id=RUN, request_id="req1", command="rm -rf /tmp/prova",
       description="Delete", choices=["once", "session", "deny"]),
    ev("assistant.completed", content="Eseguo."),
    ev("run.completed"),
    ev("done"),
])
calls = []


def hermes(req):
    calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
    if req.url.path.endswith("/chat/stream"):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=STREAM.encode())
    if req.url.path == f"/v1/runs/{RUN}/approval":
        return httpx.Response(200, json={"object": "hermes.run.approval_response", "resolved": 1})
    if req.url.path == f"/v1/runs/{RUN}/stop":
        return httpx.Response(200, json={"run_id": RUN, "status": "stopping"})
    if req.url.path == "/v1/runs/run_" + "c" * 32 + "/approval":
        return httpx.Response(409, json={"error": {"message": "Run has no pending approval"}})
    raise AssertionError(f"unexpected {req.method} {req.url}")


def events(text):
    return [e for e in (json.loads(l[5:]) for l in text.split(NL) if l.startswith("data:")) if e["type"] != "flush"]


_setup.mock(hermes)
with _setup.signed_in_client() as c:
    first = events(c.post("/api/chat", json={"message": "pulisci /tmp/prova", "session_id": "s1"}).text)

    # --- the phone comes back and asks for what it missed ---
    seen_up_to = first[2]["seq"]
    r = c.get(f"/api/runs/{RUN}/events?after={seen_up_to}")
    rest = events(r.text)
    assert [e["seq"] for e in rest] == [e["seq"] for e in first if e["seq"] > seen_up_to], rest
    assert rest[-1]["type"] == "done"
    print("ripresa: solo gli eventi dopo l'ultimo visto  OK")
    assert c.get("/api/runs/run_" + "d" * 32 + "/events").status_code == 404
    print("turno sconosciuto: 404  OK")

    # --- approval ---
    r = c.post(f"/api/runs/{RUN}/approval", json={"choice": "once", "request_id": "req1"})
    assert r.status_code == 200 and r.json()["choice"] == "once", r.text
    assert calls[-1] == ("POST", f"/v1/runs/{RUN}/approval", {"choice": "once", "request_id": "req1"}), calls[-1]
    row = next(x for x in audit.recent() if x["action"] == "approval")
    assert "rm -rf /tmp/prova" in row["detail"] and row["ok"], row
    after = events(c.get(f"/api/runs/{RUN}/events?after={first[-1]['seq']}").text)
    assert after and after[0]["type"] == "approval_done", after
    print("approvazione: inoltrata con request_id, nel registro col comando, notificata agli altri  OK")

    n = len(calls)
    assert c.post(f"/api/runs/{RUN}/approval", json={"choice": "always"}).status_code == 400
    assert c.post("/api/runs/run_x/approval", json={"choice": "once"}).status_code == 400
    assert len(calls) == n, "a refused approval reached Hermes"
    print("'always' e id malformati rifiutati senza chiamare Hermes  OK")

    r = c.post("/api/runs/run_" + "c" * 32 + "/approval", json={"choice": "deny"})
    assert r.status_code == 409 and "no pending approval" in r.json()["error"], r.text
    assert not audit.recent()[0]["ok"]
    print("approvazione già scaduta: 409 leggibile, registrata come fallita  OK")

    # --- stop ---
    r = c.post(f"/api/runs/{RUN}/stop")
    assert r.status_code == 200 and r.json()["status"] == "stopping", r.text
    assert audit.recent()[0]["action"] == "stop"
    print("stop: inoltrato e registrato  OK")

    # --- a cross-site page cannot approve on the phone's behalf ---
    r = c.post(f"/api/runs/{RUN}/approval", json={"choice": "once"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403, r.status_code
    print("approvazione da un altro sito: 403  OK")

print()
print("OK: approvazioni, stop e ripresa del turno")
