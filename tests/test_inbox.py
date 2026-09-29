"""The Inbox tab against a Beeper that answers like the real API (5.0), and its event watcher."""
import asyncio
import json
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

import _setup
import httpx
from hub import audit, inbox, push


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


NOW = time.time()
WA = "!wa_1:ba_x.local-whatsapp.localhost"
IG = "!ig_1:ba_y.local-instagram.localhost"
calls = []
beeper = {"up": True, "signed_in": False, "approve": True, "token": "tok-1"}

CHATS = [
    {"id": WA, "accountID": "wa", "network": "WhatsApp", "title": "Marco", "type": "single", "participants": {},
     "unreadCount": 2, "lastActivity": iso(NOW - 60), "imgURL": "file:///var/lib/beeper/.config/BeeperTexts/a.jpg",
     "preview": {"id": "m9", "type": "IMAGE", "text": "", "isSender": False, "senderName": "Marco"}},
    {"id": IG, "accountID": "ig", "network": "Instagram", "title": "Giulia", "type": "single", "participants": {},
     "unreadCount": 0, "lastActivity": iso(NOW - 3600), "imgURL": "file:///etc/passwd", "isMuted": True,
     "preview": {"id": "m1", "type": "TEXT", "text": "a  domani\n ciao", "isSender": True}},
    {"id": "!merged", "accountID": "wa", "network": "WhatsApp", "title": "doppione", "type": "single",
     "participants": {}, "unreadCount": 0, "mergedIntoChatID": WA},
]
MESSAGES = [
    {"id": "m3", "chatID": WA, "accountID": "wa", "senderID": "@a", "senderName": "Marco", "timestamp": iso(NOW - 60),
     "sortKey": "3", "type": "IMAGE", "text": "", "attachments": [
         {"id": "mxc://beeper.com/abc", "type": "img", "mimeType": "image/jpeg", "size": {"width": 800, "height": 600}}]},
    {"id": "m2", "chatID": WA, "accountID": "wa", "senderID": "@me", "isSender": True, "timestamp": iso(NOW - 120),
     "sortKey": "2", "type": "TEXT", "text": "ci vediamo?", "sendStatus": {"status": "SUCCESS"},
     "reactions": [{"id": "@a", "reactionKey": "👍", "participantID": "@a"}]},
    {"id": "r1", "chatID": WA, "accountID": "wa", "senderID": "@a", "timestamp": iso(NOW - 110), "sortKey": "2a",
     "type": "REACTION", "text": "👍"},
    {"id": "h1", "chatID": WA, "accountID": "wa", "senderID": "@a", "timestamp": iso(NOW - 100), "sortKey": "2b",
     "isHidden": True, "text": "nascosto"},
]


def handler(req: httpx.Request):
    path, q = req.url.path, parse_qs(req.url.query.decode())
    auth = req.headers.get("authorization")
    body = json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") else None
    calls.append((req.method, path, q, body, auth))
    if not beeper["up"]:
        raise httpx.ConnectError("refused")
    if path == "/oauth/register":
        return httpx.Response(201, json={"client_id": "cid-1", "client_name": body["client_name"]})
    if path == "/oauth/authorize/callback":
        assert body["codeChallengeMethod"] == "S256" and body["redirectUri"] == inbox.REDIRECT
        return httpx.Response(200, json={"code": "code-1", "state": body["state"]} if beeper["approve"]
                              else {"error": "access_denied", "state": body["state"]})
    if path == "/oauth/token":
        form = parse_qs(req.content.decode())
        assert form["code"] == ["code-1"] and form["code_verifier"][0] and form["client_id"] == ["cid-1"]
        return httpx.Response(200, json={"access_token": beeper["token"], "token_type": "Bearer",
                                         "scope": "read write", "expires_in": 30 * 86400})
    if path == "/oauth/revoke":
        return httpx.Response(200, json={})
    if path == "/v1/app/setup":
        if beeper["signed_in"] and not auth:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"state": "ready" if beeper["signed_in"] else "needs-login"})
    if auth != f"Bearer {beeper['token']}":
        return httpx.Response(401, json={"error": "unauthorized"})
    if path == "/v1/accounts":
        return httpx.Response(200, json=[
            {"accountID": "wa", "network": "WhatsApp", "bridge": {"id": "wa", "type": "whatsapp"},
             "user": {"id": "@me", "fullName": "Lorenzo"}, "status": "connected"},
            {"accountID": "ig", "bridge": {"id": "ig", "type": "instagram", "name": "Instagram"},
             "user": {"id": "@me2", "username": "lore"}, "status": "reconnect_required", "statusText": "Accedi di nuovo"}])
    if path in ("/v1/chats", "/v1/chats/search"):
        return httpx.Response(200, json={"items": CHATS, "hasMore": True, "oldestCursor": "c-old"})
    if path == f"/v1/chats/{WA}":
        return httpx.Response(200, json=CHATS[0])
    if path == f"/v1/chats/{IG}":
        return httpx.Response(200, json=CHATS[1])
    if path == f"/v1/chats/{WA}/messages" and req.method == "GET":
        return httpx.Response(200, json={"items": MESSAGES, "hasMore": False, "oldestCursor": "x"})
    if path == f"/v1/chats/{WA}/messages" and req.method == "POST":
        return httpx.Response(200, json={"chatID": WA, "pendingMessageID": "p-1"})
    if path == f"/v1/chats/{WA}/read":
        return httpx.Response(200, json=CHATS[0])
    if path == "/v1/assets/serve":
        headers = {"content-type": "image/jpeg", "accept-ranges": "bytes"}
        if req.headers.get("range"):
            return httpx.Response(206, content=b"JP", headers={**headers, "content-range": "bytes 0-1/4"})
        return httpx.Response(200, content=b"JPEG", headers=headers)
    raise AssertionError(f"unexpected {req.method} {req.url}")


inbox.transport = httpx.MockTransport(handler)
SAME = {"Sec-Fetch-Site": "same-origin"}


async def settle():
    for _ in range(50):
        if not inbox.connector.pending:
            return
        await asyncio.sleep(0.01)


with _setup.signed_in_client() as c:
    s = c.get("/api/inbox/status").json()
    assert s["setup"] == "needs-login" and not s["connected"] and s["accounts"] == [], s
    assert c.get("/api/inbox/chats").json()["code"] == "connect"
    beeper["up"] = False
    assert c.get("/api/inbox/status").json()["beeper"] == "down"
    beeper["up"] = True
    print("prima del collegamento: stato di Beeper letto senza token, chat chiuse, Beeper spento distinto  OK")

    # Approval refused in Beeper's window, then granted.
    beeper["approve"] = False
    assert c.post("/api/inbox/connect", headers=SAME).json() == {"pending": True}
    c.portal.call(settle)
    s = c.get("/api/inbox/status").json()
    assert "rifiutata" in s["error"] and not s["connected"], s
    beeper["signed_in"] = True      # signed in on the PC: the setup call now wants a token
    s = c.get("/api/inbox/status").json()
    assert s["beeper"] == "up" and s["setup"] == "ready" and not s["connected"], s
    beeper["approve"] = True
    c.post("/api/inbox/connect", headers=SAME)
    c.portal.call(settle)
    assert inbox._row()["token"] == "tok-1" and inbox._row()["token_expires"] > NOW + 29 * 86400
    assert [p for _, p, *_ in calls].count("/oauth/register") == 1, "the client is registered once"
    s = c.get("/api/inbox/status").json()
    assert s["connected"] and s["setup"] == "ready" and s["error"] is None, s
    assert s["accounts"] == [
        {"id": "wa", "network": "WhatsApp", "name": "Lorenzo", "status": "connected", "status_text": ""},
        {"id": "ig", "network": "Instagram", "name": "lore", "status": "reconnect_required",
         "status_text": "Accedi di nuovo"}], s["accounts"]
    assert any(r["action"] == "inbox-connect" for r in audit.recent())
    print("collegamento: PKCE, rifiuto mostrato, token conservato con la scadenza, account leggibili  OK")

    d = c.get("/api/inbox/chats").json()
    assert [x["id"] for x in d["chats"]] == [WA, IG] and d["older"] == "c-old", "merged copies hidden"
    wa, ig = d["chats"]
    assert wa["unread"] == 2 and wa["preview"] == {"text": "📷 Foto", "mine": False, "sender": "Marco"}
    assert wa["avatar"].startswith("/api/inbox/asset?u=file%3A%2F%2F%2Fvar%2Flib%2Fbeeper")
    assert ig["avatar"] is None, "a file outside Beeper's home is never offered"
    assert ig["preview"]["text"] == "a domani ciao" and ig["muted"]
    assert calls[-1][1] == "/v1/chats" and calls[-1][2]["limit"] == ["50"]
    c.get("/api/inbox/chats?q=giu&cursor=c-old")
    assert calls[-1][1] == "/v1/chats/search" and calls[-1][2]["query"] == ["giu"] \
        and calls[-1][2]["direction"] == ["before"] and calls[-1][2]["inbox"] == ["primary"]
    c.get("/api/inbox/chats?unread=true&account=wa")
    assert calls[-1][2]["unreadOnly"] == ["true"] and calls[-1][2]["accountIDs"] == ["wa"]
    print("elenco chat: anteprime leggibili, doppioni nascosti, avatar solo dalla cartella di Beeper, "
          "ricerca e non lette  OK")

    d = c.get(f"/api/inbox/chats/{WA.replace('!', '%21')}").json()
    assert d["chat"]["title"] == "Marco" and d["older"] is None
    assert [m["id"] for m in d["messages"]] == ["m2", "m3"], "chronological, no reactions or hidden ones"
    m2, m3 = d["messages"]
    assert m2["mine"] and m2["reactions"] == [{"key": "👍", "count": 1, "mine": False}] and m2["status"] == "SUCCESS"
    assert m3["attachments"][0]["url"] == "/api/inbox/asset?u=mxc%3A%2F%2Fbeeper.com%2Fabc"
    assert m3["attachments"][0]["w"] == 800
    assert c.get("/api/inbox/chats/a%2Fb").status_code in (400, 404)
    print("una chat: in ordine, reazioni sul messaggio, allegati passati dall'Hub  OK")

    n = len(calls)
    assert c.post(f"/api/inbox/chats/{WA}/messages", json={"text": "  "}, headers=SAME).status_code == 400
    assert c.post(f"/api/inbox/chats/{WA}/messages", json={"text": "arrivo"},
                  headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert len(calls) == n, "refused sends never reached Beeper"
    r = c.post(f"/api/inbox/chats/{WA}/messages", json={"text": "arrivo alle 8", "reply_to": "m3"}, headers=SAME)
    assert r.json() == {"ok": True, "pending_id": "p-1"}
    assert calls[-1][3] == {"text": "arrivo alle 8", "replyToMessageID": "m3"}
    assert any(e["action"] == "inbox-send" and "chars=13" in e["detail"] for e in audit.recent())
    c.post(f"/api/inbox/chats/{WA}/read", json={"message_id": "m3"}, headers=SAME)
    assert calls[-1][1] == f"/v1/chats/{WA}/read" and calls[-1][3] == {"messageID": "m3"}
    print("invio: vuoti e da altri siti fermati, risposta a un messaggio, registrato nell'audit; letto  OK")

    r = c.get("/api/inbox/asset?u=mxc%3A%2F%2Fbeeper.com%2Fabc")
    assert r.content == b"JPEG" and r.headers["content-type"] == "image/jpeg" and "private" in r.headers["cache-control"]
    r = c.get("/api/inbox/asset?u=mxc%3A%2F%2Fbeeper.com%2Fabc", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.headers["content-range"] == "bytes 0-1/4"
    n = len(calls)
    for bad in ("file:///etc/passwd", "file:///var/lib/beeper/../../etc/shadow", "https://example.com/x",
                "file:///var/lib/beeperx/a"):
        assert c.get("/api/inbox/asset", params={"u": bad}).status_code == 400, bad
    assert len(calls) == n
    print("file: dai media di Beeper, anche a pezzi per l'audio; nessun altro file del server  OK")

    beeper["token"] = "tok-2"      # revoked in Beeper
    assert c.get("/api/inbox/chats").json()["code"] == "connect"
    assert c.get("/api/inbox/status").json()["connected"] is False
    beeper["token"] = "tok-1"
    print("token revocato in Beeper: l'app chiede di ricollegare  OK")


# ── the watcher: what reaches the open app, what rings ─────────────
rung = []


async def fake_send_all(title, body, tag="hub", url="/#/server"):
    rung.append((title, body, tag, url))
    return 1


push.send_all = fake_send_all


def upsert(mid, chat=WA, **m):
    base = {"id": mid, "chatID": chat, "accountID": "wa", "senderID": "@a", "senderName": "Marco",
            "timestamp": iso(time.time() - 5), "sortKey": mid, "type": "TEXT", "text": "ciao!"}
    return json.dumps({"type": "message.upserted", "seq": 1, "ts": iso(time.time()), "chatID": chat,
                       "ids": [mid], "entries": [{**base, **m}]})


async def watch():
    w = inbox.Watcher()
    app_open = inbox.Subscriber()
    w.subscribers.add(app_open)
    await w.handle(upsert("n1"))
    item = app_open.queue.get_nowait()
    assert item["t"] == "message" and item["chat"] == WA and item["message"]["text"] == "ciao!"
    assert rung == [("Marco (WhatsApp)", "ciao!", f"inbox:{WA}", "/#/inbox/%21wa_1%3Aba_x.local-whatsapp.localhost")]
    await w.handle(upsert("n1", text="ciao! (modificato)"))     # an edit of the same message
    await w.handle(upsert("n2", isSender=True))                  # my own, from another device
    await w.handle(upsert("n3", timestamp=iso(time.time() - 3600)))   # history arriving
    await w.handle(upsert("n4", chat=IG, senderName="Giulia"))   # muted chat
    await w.handle(upsert("n5", type="REACTION"))
    assert len(rung) == 1, rung
    await w.handle(upsert("n6"))                                  # same chat, seconds later
    assert len(rung) == 1, "one ring per chat in a short span"
    w.last_ring.clear()
    w.subscribers.add(inbox.Subscriber(chat=WA))                 # the chat is open on the phone
    await w.handle(upsert("n7"))
    assert len(rung) == 1
    inbox._update(notify=0)
    w.subscribers = {app_open}
    await w.handle(upsert("n8"))
    assert len(rung) == 1, "notifications off"
    inbox._update(notify=1)
    await w.handle(json.dumps({"type": "chat.upserted", "seq": 2, "ts": iso(time.time()), "chatID": WA, "ids": [WA]}))
    kinds = []
    while not app_open.queue.empty():
        kinds.append(app_open.queue.get_nowait()["t"])
    assert kinds[-1] == "chat" and "message" in kinds
    await w.handle("non json")


asyncio.run(watch())
print("eventi: nuovi messaggi all'app aperta; notifica solo per i messaggi nuovi degli altri, una volta, "
      "non per chat silenziate, già aperte o a notifiche spente  OK")

print()
print("OK: Inbox")
