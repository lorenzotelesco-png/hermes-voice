"""The File tab's routes: the vault through its helper, Hermes' folders through the dashboard."""
import base64

import _setup
import httpx
from hub import audit, files

asked = []
ANSWERS = {}


async def fake_ask(action, timeout=90, **fields):
    asked.append((action, fields))
    answer = ANSWERS.get(action, {"ok": True})
    return dict(answer(fields) if callable(answer) else answer)


# ── helper not installed ───────────────────────────────────────────
with _setup.signed_in_client() as c:
    r = c.get("/api/vault/index")
    assert r.status_code == 503 and "deploy" in r.json()["error"], r.text
print("aiutante del vault assente: 503 con istruzioni  OK")

files.ask = fake_ask
png = b"\x89PNG\r\n\x1a\n" + b"x" * 50
ANSWERS.update({
    "index": {"ok": True, "files": [{"path": "Idee.md", "size": 5, "mtime": 1}], "dirs": [], "attachments": "Allegati"},
    "read": lambda f: ({"ok": True, "path": f["path"], "text": "# Idee", "sha": "a" * 40, "mtime": 1}
                       if f["path"] == "Idee.md" else {"ok": False, "error": "il file non esiste", "status": 404}),
    "raw": {"ok": True, "mime": "image/png", "data": base64.b64encode(png).decode()},
    "save": lambda f: ({"ok": False, "conflict": True, "status": 409, "error": "la nota è cambiata altrove",
                        "theirs": "versione del PC", "theirs_sha": "b" * 40}
                       if f["base"] == "vecchia" else
                       {"ok": True, "path": f["path"], "sha": "c" * 40, "merged": False, "text": None,
                        "pushed": True, "push_error": None, "copies": []}),
    "upload": lambda f: {"ok": True, "path": "Allegati/" + f["name"], "name": f["name"], "pushed": True,
                         "push_error": None, "copies": []},
    "capture": {"ok": True, "path": "Daily/2026-09-26.md", "created": True, "pushed": True,
                "push_error": None, "copies": []},
    "sync": {"ok": True, "ahead": 0, "behind": 0, "pushed": True, "copies": ["Idee (conflitto telefono x).md"]},
})

with _setup.signed_in_client() as c:
    assert c.get("/api/vault/index").json()["files"][0]["path"] == "Idee.md"
    assert c.get("/api/vault/note?path=Idee.md").json()["sha"] == "a" * 40
    r = c.get("/api/vault/note?path=Manca.md")
    assert r.status_code == 404 and r.json()["error"] == "il file non esiste"
    print("indice e lettura inoltrati, i rifiuti dell'aiutante arrivano col loro stato  OK")

    r = c.get("/api/vault/raw?path=Allegati/x.png")
    assert r.content == png and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "private" in r.headers["cache-control"]
    print("immagini servite coi loro byte e tipo, in cache solo sul telefono  OK")

    r = c.post("/api/vault/note", json={"path": "Idee.md", "text": "nuovo", "base": "vecchia"})
    assert r.status_code == 409 and r.json()["theirs"] == "versione del PC" and r.json()["conflict"], r.text
    assert audit.recent()[0]["action"] == "vault-save" and not audit.recent()[0]["ok"]
    r = c.post("/api/vault/note", json={"path": "Idee.md", "text": "nuovo", "base": "a" * 40})
    assert r.status_code == 200 and r.json()["pushed"], r.text
    assert asked[-1] == ("save", {"path": "Idee.md", "text": "nuovo", "base": "a" * 40})
    assert "Idee.md" in audit.recent()[0]["detail"] and audit.recent()[0]["ok"]
    r = c.post("/api/vault/note", json={"path": "Idee.md", "text": "x", "base": None},
               headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    print("salvataggio: conflitto 409 con la versione del PC, riuscito nel registro, da altri siti 403  OK")

    r = c.post("/api/vault/upload", files={"file": ("image.jpg", b"\xff\xd8jpeg", "image/jpeg")},
               data={"name": "Foto 2026-09-25 19-30.jpg"})
    assert r.json()["path"] == "Allegati/Foto 2026-09-25 19-30.jpg", r.text
    assert base64.b64decode(asked[-1][1]["data"]) == b"\xff\xd8jpeg"
    r = c.post("/api/vault/upload", files={"file": ("grande.jpg", b"x" * (files.MAX_UPLOAD + 1), "image/jpeg")})
    assert r.status_code == 413 and asked[-1][0] == "upload" and asked[-1][1]["name"] != "grande.jpg"
    assert audit.recent()[0]["action"] == "vault-upload"
    print("allegati: nome scelto dal telefono, oltre 15 MB fermati prima dell'aiutante  OK")

    r = c.post("/api/vault/capture", json={"text": "latte", "date": "2026-09-26", "time": "09:15"})
    assert r.json()["path"] == "Daily/2026-09-26.md"
    assert asked[-1] == ("capture", {"text": "latte", "date": "2026-09-26", "time": "09:15"})
    r = c.post("/api/vault/sync").json()
    assert r["copies"] and "conflitto" in audit.recent()[0]["detail"]
    print("nota rapida inoltrata; sincronizzazione con copie di conflitto nel registro  OK")

# ── Hermes' folders through the dashboard ─────────────────────────
BASE = "/root/.hermes/hermes-agent"
dash = []


def dashboard(req):
    p, path = req.url.path, req.url.params.get("path")
    dash.append((p, path))
    if p == "/api/fs/list":
        return httpx.Response(200, json={"entries": [
            {"name": "agent", "path": f"{BASE}/agent", "isDirectory": True},
            {"name": "README.md", "path": f"{BASE}/README.md", "isDirectory": False},
            {"name": "fuga", "path": "/etc/fuga", "isDirectory": True},
        ]})
    if p == "/api/fs/read-text":
        real = "/etc/passwd" if path.endswith("collegamento") else path
        return httpx.Response(200, json={"path": real, "text": "ciao", "binary": False, "truncated": False,
                                         "byteSize": 4, "language": "markdown"})
    raise AssertionError(f"unexpected {req.url}")


_setup.mock(dashboard)
with _setup.signed_in_client() as c:
    assert [r["id"] for r in c.get("/api/files/roots").json()["roots"]] == ["hermes", "logs"]
    d = c.get("/api/files/hermes/list?path=").json()
    assert [e["path"] for e in d["entries"]] == ["agent", "README.md"] and dash[-1] == ("/api/fs/list", BASE), d
    d = c.get("/api/files/hermes/read?path=README.md").json()
    assert d["text"] == "ciao" and dash[-1][1] == f"{BASE}/README.md"
    n = len(dash)
    for bad in ("../.env", "agent/../../.env", "a\\b"):
        assert c.get(f"/api/files/hermes/read?path={bad}").status_code == 400, bad
    assert c.get("/api/files/root/list?path=").status_code == 404
    assert len(dash) == n, "a refused path reached the dashboard"
    assert c.get("/api/files/hermes/read?path=collegamento").status_code == 403
    print("cartelle di Hermes: solo dentro le radici, voci che escono scartate, "
          "percorsi strani fermati prima della dashboard  OK")

print()
print("OK: tab File")
