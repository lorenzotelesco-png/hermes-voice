"""Session list, search and transcripts, reduced to what the phone draws."""
import json

import _setup
import httpx

MESSAGES = [
    # Deliberately out of order: the thread must come back chronological.
    {"id": "12", "role": "assistant", "content": "Fa 36.", "timestamp": 12.0},
    {"id": "9", "role": "user", "content": "Quanto fa 12 per 3?", "timestamp": 9.0},
    {"id": "10", "role": "assistant", "content": "", "timestamp": 10.0, "tool_calls": [
        {"id": "c1", "function": {"name": "terminal", "arguments": json.dumps({"command": "echo $((12*3))"})}},
        {"id": "c2", "function": {"name": "web_search", "arguments": json.dumps({"query": "meteo milano"})}},
    ]},
    {"id": "11", "role": "tool", "tool_call_id": "c1", "timestamp": 11.0,
     "content": json.dumps({"output": "", "exit_code": -1, "error": "BLOCKED: timed out"})},
    {"id": "11b", "role": "tool", "tool_call_id": "c2", "timestamp": 11.1,
     "content": json.dumps({"results": ["x" * 50000]})},
    {"id": "13", "role": "user", "content": "nota interna", "timestamp": 13.0, "display_kind": "hidden"},
    {"id": "14", "role": "user", "timestamp": 14.0,
     "content": [{"type": "text", "text": "e questa foto?"}, {"type": "image_url", "image_url": {"url": "data:..."}}]},
]


def hermes(req):
    p = req.url.path
    if req.url.host == "127.0.0.1" and req.url.port == 9119:
        assert req.headers["x-hermes-session-token"] == "k-dash"
        assert p == "/api/sessions/search" and req.url.params["q"] == "canberra"
        return httpx.Response(200, json={"results": [
            {"session_id": "s1", "title": "Australia", "source": "api_server", "snippet": "è >>>Canberra<<<", "last_active": 5},
            {"session_id": "s1", "title": "Australia", "source": "api_server", "snippet": "di nuovo", "last_active": 5},
            {"session_id": "d7", "title": None, "source": "discord", "snippet": ">>>Canberra<<< e Sydney", "last_active": 3},
        ]})
    assert req.headers["authorization"] == "Bearer k-agent"
    if p == "/api/sessions":
        assert req.url.params["limit"] == "100", "limit not clamped"
        return httpx.Response(200, json={"object": "list", "has_more": True, "data": [
            {"id": "s1", "source": "api_server", "title": "Australia", "preview": "Qual è la capitale",
             "last_active": 5.0, "message_count": 4, "system_prompt": "SEGRETO", "input_tokens": 99},
        ]})
    if p == "/api/sessions/s1":
        return httpx.Response(200, json={"session": {"id": "s1", "source": "discord", "title": "Conti"}})
    if p == "/api/sessions/s1/messages":
        return httpx.Response(200, json={"data": MESSAGES})
    if p.startswith("/api/sessions/nope"):
        return httpx.Response(404, json={"error": {"message": "Session not found: nope"}})
    raise AssertionError(f"unexpected {req.method} {req.url}")


_setup.mock(hermes)
with _setup.signed_in_client() as c:
    r = c.get("/api/sessions?limit=5000")
    j = r.json()
    assert r.status_code == 200 and j["has_more"] is True, r.text
    s = j["sessions"][0]
    assert s["id"] == "s1" and s["title"] == "Australia" and s["source"] == "api_server"
    assert "system_prompt" not in s and "SEGRETO" not in r.text, "session internals leaked"
    print("elenco: campi ridotti, limite forzato  OK")

    r = c.get("/api/sessions/search?q=canberra")
    hits = r.json()["results"]
    assert [h["session_id"] for h in hits] == ["s1", "d7"], hits
    assert hits[0]["snippet"] == "è >>>Canberra<<<"
    assert c.get("/api/sessions/search?q=a").json() == {"results": []}
    print("ricerca: un risultato per conversazione, niente query di una lettera  OK")

    r = c.get("/api/sessions/s1")
    j = r.json()
    assert j["session"]["title"] == "Conti"
    items = j["items"]
    for it in items:
        print("  ", it)
    assert [i["role"] for i in items] == ["user", "tool", "tool", "assistant", "user"], items
    assert items[0]["text"] == "Quanto fa 12 per 3?"
    assert items[1] == {"role": "tool", "name": "terminal", "preview": "echo $((12*3))", "state": "error"}
    assert items[2] == {"role": "tool", "name": "web_search", "preview": "meteo milano", "state": "done"}
    assert items[4]["text"] == "e questa foto?"
    assert "nota interna" not in r.text, "hidden message shown"
    assert "xxxxxxxxxx" not in r.text, "tool output sent to the phone"
    print("trascrizione: ordine, schede strumenti con esito, niente output né messaggi nascosti  OK")

    assert c.get("/api/sessions/nope").status_code == 404
    assert c.get("/api/sessions/bad%20id").status_code == 400
    print("sessione inesistente 404, id malformato 400  OK")

print()
print("OK: elenco, ricerca e trascrizioni")
