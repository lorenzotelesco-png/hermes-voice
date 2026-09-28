"""Hey Hermes: the Shortcut's key, the conversation it continues, and what it says when it cannot wait."""
import asyncio
import json
import time

import _setup
import httpx
from hub import assistant, audit, push

NL = chr(10)
RUN = "run_" + "e" * 32


def ev(name, **data):
    return f"event: {name}{NL}data: {json.dumps(data)}{NL}{NL}".encode()


def stream(*events):
    return b"".join(events)


async def slow_stream():
    yield ev("run.started", run_id=RUN)
    yield ev("assistant.delta", delta="Cerco")
    await asyncio.sleep(0.6)
    yield ev("assistant.completed", content="Ho trovato **tre** voli per Hangzhou.")
    yield ev("run.completed")
    yield ev("done")


created, turns = [], []


def hermes(req):
    p = req.url.path
    if req.method == "POST" and p == "/api/sessions":
        created.append(1)
        return httpx.Response(200, json={"session": {"id": f"api_{len(created)}_abc"}})
    if p.endswith("/chat/stream"):
        body = json.loads(req.content)
        turns.append((p.split("/")[3], body["message"], body.get("system_message") or ""))
        msg = body["message"]
        if msg == "giù":
            return httpx.Response(502, text="bad gateway")
        if msg == "lungo":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=slow_stream())
        if msg == "cancella i log":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream(
                ev("run.started", run_id=RUN),
                ev("approval.request", run_id=RUN, request_id="r1", command="rm /var/log/x.log",
                   description="Delete", choices=["once", "deny"]),
                ev("assistant.completed", content="Fatto."), ev("run.completed"), ev("done")))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream(
            ev("run.started", run_id="run_" + "f" * 32),
            ev("assistant.delta", delta="Sono le "),
            ev("assistant.completed", content="Sono le **dieci**."), ev("run.completed"), ev("done")))
    raise AssertionError(f"unexpected {req.method} {req.url}")


sent = []


async def fake_send_all(title, body, tag="hub", url="/#/server"):
    sent.append((title, body, url))
    return 1


_setup.mock(hermes)
push.send_all = fake_send_all
subs = []
push.subscriptions = lambda: subs

with _setup.anon_client() as anon, _setup.signed_in_client() as app:
    ask = lambda text, key=None, **kw: anon.post(  # noqa: E731
        assistant.ASK_PATH, json={"text": text, **kw},
        headers={"Authorization": f"Bearer {key}"} if key else {})

    # ── the key ────────────────────────────────────────────────────
    assert ask("ciao").status_code == 401
    assert ask("ciao", key="inventata").status_code == 401
    assert app.get("/api/assistant/key").json()["configured"] is False
    assert app.post("/api/assistant/key", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    key = app.post("/api/assistant/key").json()["key"]
    assert len(key) >= 40 and app.get("/api/assistant/key").json()["configured"]
    assert key not in json.dumps(audit.recent()), "the key reached the audit log"
    print("chiave: creata solo dall'app, mostrata una volta, non nel registro  OK")

    r = anon.get("/api/sessions", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401, "the Shortcut's key opened another path"
    r = anon.post("/api/assistant/key", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401
    print("la chiave della Scorciatoia apre solo /api/assistant/ask  OK")

    # ── a conversation ─────────────────────────────────────────────
    r = ask("che ore sono", key=key).json()
    assert r == {"reply": "Sono le dieci.", "end": 0, "session_id": "api_1_abc"}, r
    assert turns[-1][0] == "api_1_abc" and "Rispondi SEMPRE e SOLO in italiano" in turns[-1][2]
    r = ask("e domani?", key=key).json()
    assert r["session_id"] == "api_1_abc" and len(created) == 1, "a follow-up opened a new session"
    assert ask("ricomincia", key=key, new=True).json()["session_id"] == "api_2_abc"
    assistant._update(last_turn=time.time() - assistant.CONTINUE_S - 5)
    assert ask("dopo un po'", key=key).json()["session_id"] == "api_3_abc"
    print("risposta pulita per la voce; stessa sessione entro 10 minuti, nuova dopo o se chiesta  OK")

    n = len(turns)
    for bye in ("Grazie!", "basta", "ok, grazie."):
        assert ask(bye, key=key).json() == {"reply": "A dopo.", "end": 1}, bye
    assert ask("", key=key).json()["end"] == 1
    assert len(turns) == n, "a goodbye reached Hermes"
    print("\"grazie\", \"basta\"… chiudono senza chiamare Hermes  OK")

    # ── what needs the screen ──────────────────────────────────────
    notices = lambda: [s for s in sent if s[0] == "Hermes chiede una conferma"]  # noqa: E731
    r = ask("cancella i log", key=key).json()
    assert r["end"] == 1 and "apri l'app" in r["reply"] and not notices(), r
    subs.append({"endpoint": "x"})
    r = ask("cancella i log", key=key).json()
    assert "notifica" in r["reply"], r
    title, body, url = notices()[-1]
    assert "rm /var/log/x.log" in body
    assert url == f"/#/chat/turno/{RUN}/{r['session_id']}", url
    print("approvazione: la Scorciatoia lo dice, la notifica apre l'app su quel turno  OK")

    sent.clear()
    assistant.WAIT_S = 0.2
    r = ask("lungo", key=key).json()
    assert r["end"] == 1 and "ancora lavorando" in r["reply"], r
    time.sleep(1.2)
    assert sent and sent[-1][0] == "Hermes ha risposto" and sent[-1][1] == "Ho trovato tre voli per Hangzhou.", sent
    assert sent[-1][2] == f"/#/chat/apri/{r['session_id']}"
    assistant.WAIT_S = 40
    print("lavoro lungo: la Scorciatoia non aspetta, la risposta arriva come notifica  OK")

    r = ask("giù", key=key)
    assert r.status_code == 200 and "Non riesco a raggiungere Hermes" in r.json()["reply"], r.text
    print("Hermes irraggiungibile: una frase da dire, non un errore muto  OK")

    # ── limits and revocation ──────────────────────────────────────
    assistant._recent[:] = [time.time()] * assistant.RATE_MAX
    assert ask("ancora", key=key).status_code == 429
    assistant._recent.clear()
    assert app.delete("/api/assistant/key").json() == {"ok": True}
    assert ask("ciao", key=key).status_code == 401
    assert any(x["action"] == "ask" and x["login"] == "shortcut" for x in audit.recent(50))
    print("più di 40 richieste in 10 minuti fermate; chiave revocata dall'app non entra più  OK")

print()
print("OK: Hey Hermes")
