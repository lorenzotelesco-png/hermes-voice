"""The vault helper (deploy/vault/hermes-hub-vault) against real git repos.

Three repositories stand in for the real ones: a bare "GitHub", the server's
clone the helper works on, and the PC's clone where Obsidian edits and pulls.
"""
import base64
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="hub-vault-")
GITHUB, SERVER, PC = (os.path.join(TMP, n) for n in ("github.git", "server", "pc"))


def run(cwd, *args):
    r = subprocess.run(["git", "-c", "init.defaultBranch=master", *args], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def pc_write(rel, text):
    path = os.path.join(PC, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def pc_read(rel):
    with open(os.path.join(PC, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def pc_push(message="pc"):
    run(PC, "add", "-A")
    run(PC, "commit", "-q", "-m", message)
    run(PC, "push", "-q", "origin", "master")


def pc_pull():
    run(PC, "pull", "-q", "--no-rebase", "origin", "master")


def server_read(rel):
    with open(os.path.join(SERVER, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


loader = importlib.machinery.SourceFileLoader("vault_helper", os.path.join(ROOT, "deploy", "vault", "hermes-hub-vault"))
h = importlib.util.module_from_spec(importlib.util.spec_from_loader("vault_helper", loader))
loader.exec_module(h)
h.VAULT = SERVER

# ── three repos, like GitHub, the VPS and the PC ───────────────────
run(TMP, "init", "-q", "--bare", GITHUB)
# No line-ending conversion: Git for Windows turns it on globally.
run(TMP, "clone", "-q", "--config", "core.autocrlf=false", GITHUB, PC)
run(PC, "config", "user.name", "Lorenzo")
run(PC, "config", "user.email", "pc@example.invalid")
pc_write(".obsidian/app.json", '{"alwaysUpdateLinks": true}')
pc_write(".obsidian/plugins/periodic-notes/data.json",
         '{"calendarSets": [{"day": {"enabled": true, "folder": "Daily", "templatePath": "Templates/Daily.md"}}]}')
pc_write("Templates/Daily.md", '# <% tp.date.now("YYYY-MM-DD") %>\n\n## Task\n- [ ] \n\n## Note libere\n\n\n---\n\n[[Dashboard]]\n')
pc_write("Idee.md", "# Idee\n\nuno\ndue\ntre\nquattro\ncinque\nsei\nsette\n")
pc_write("Progetti/Hermes Hub.md", "---\ntags:\n  - perche\n---\nL'Hub serve perché tutto stia sul telefono.\n")
pc_write("Daily/2026-09-24.md", "# 2026-09-24\n\nNiente sezione per le note.\n")
pc_push("vault iniziale")
run(TMP, "clone", "-q", "--config", "core.autocrlf=false", GITHUB, SERVER)

# ── reading ────────────────────────────────────────────────────────
ix = h.handle({"action": "index"})
paths = [f["path"] for f in ix["files"]]
assert ix["ok"] and "Idee.md" in paths and "Progetti/Hermes Hub.md" in paths, paths
assert not any(p.startswith(".") for p in paths) and "Progetti" in ix["dirs"] and ix["attachments"] == "Allegati"
note = h.handle({"action": "read", "path": "Idee.md"})
assert note["text"].startswith("# Idee") and note["sha"] == run(SERVER, "hash-object", "Idee.md").strip()
print("indice senza file nascosti, lettura con l'id git della versione  OK")

for bad in ("../fuori.md", "/etc/passwd", ".git/config", ".obsidian/app.json", "Idee.md/../../x.md",
            "..\\x.md", "", "a//b.md"):
    r = h.handle({"action": "read", "path": bad})
    assert not r["ok"] and r["status"] in (400, 403), (bad, r)
try:
    os.symlink(TMP, os.path.join(SERVER, "fuga"), target_is_directory=True)
except OSError:
    print("  (collegamenti simbolici non creabili qui: prova saltata)")
else:
    r = h.handle({"action": "read", "path": "fuga/github.git/config"})
    assert not r["ok"] and r["status"] == 403, r
    r = h.handle({"action": "save", "path": "fuga/x.md", "text": "x", "base": None})
    assert not r["ok"] and r["status"] == 403, r
    os.remove(os.path.join(SERVER, "fuga")) if os.path.islink(os.path.join(SERVER, "fuga")) else os.rmdir(os.path.join(SERVER, "fuga"))
assert h.handle({"action": "delete", "path": "Idee.md"})["status"] == 403
print("percorsi fuori dal vault, nascosti o strani rifiutati; azioni sconosciute rifiutate  OK")

# ── saving: the phase's first criterion ────────────────────────────
text = note["text"].replace("tre\n", "tre, dal telefono\n")
r = h.handle({"action": "save", "path": "Idee.md", "text": text, "base": note["sha"]})
assert r["ok"] and r["pushed"] and not r["merged"] and not r["copies"], r
pc_pull()
assert "tre, dal telefono" in pc_read("Idee.md")
assert run(PC, "log", "-1", "--format=%an|%s").strip() == "Hermes Hub|Hub: modifica Idee.md"
print("nota modificata dal telefono: commit e push, il PC la vede dopo il suo pull  OK")

# the PC changes another line meanwhile: merged, nothing lost
base = r["sha"]
pc_write("Idee.md", pc_read("Idee.md").replace("sette\n", "sette, dal PC\n"))
pc_push()
mine = text.replace("uno\n", "uno, di nuovo dal telefono\n")
r = h.handle({"action": "save", "path": "Idee.md", "text": mine, "base": base})
assert r["ok"] and r["merged"] and r["pushed"], r
assert "uno, di nuovo dal telefono" in r["text"] and "sette, dal PC" in r["text"], r["text"]
pc_pull()
assert pc_read("Idee.md") == r["text"]
print("modifiche su righe diverse dal PC e dal telefono: unite da sole  OK")

# ── the phase's second criterion: a conflict loses neither version ─
base = r["sha"]
pc_write("Idee.md", pc_read("Idee.md").replace("quattro\n", "quattro (PC)\n"))
pc_push()
pc_version = pc_read("Idee.md")
phone = r["text"].replace("quattro\n", "quattro (telefono)\n")
r = h.handle({"action": "save", "path": "Idee.md", "text": phone, "base": base})
assert not r["ok"] and r["conflict"] and r["status"] == 409 and r["theirs"] == pc_version, r
assert server_read("Idee.md") == pc_version, "the server's copy was touched"
r = h.handle({"action": "save", "path": "Idee (telefono).md", "text": phone, "base": None})
assert r["ok"] and r["pushed"], r
pc_pull()
assert pc_read("Idee.md") == pc_version and pc_read("Idee (telefono).md") == phone
print("conflitto sulla stessa riga: nulla scritto, restituita la versione del PC; "
      "salvata come copia, sul PC ci sono tutte e due  OK")

# a "new" note that already exists, and a note gone from disk
r = h.handle({"action": "save", "path": "Idee.md", "text": "x", "base": None})
assert r["conflict"] and r["theirs"] == pc_version
r = h.handle({"action": "save", "path": "Sparita.md", "text": "x", "base": "0" * 40})
assert r["conflict"] and r["theirs"] is None
assert h.handle({"action": "save", "path": "script.sh", "text": "x", "base": None})["status"] == 400
assert h.handle({"action": "save", "path": "Idee.md", "text": "x", "base": "--output=/tmp/x"})["status"] == 400
print("nuova nota già esistente, nota sparita, estensioni e versioni non valide: fermati  OK")

# ── GitHub moves under an unpushed commit: both versions kept ──────
ours_text = server_read("Idee.md").replace("cinque\n", "cinque (server)\n")
h.write("Idee.md", ours_text.encode())
h.commit(["Idee.md"], "commit rimasto sul server")
h.write("Solo server.md", b"solo qui\n")
h.commit(["Solo server.md"], "altro commit rimasto sul server")
pc_write("Idee.md", pc_read("Idee.md").replace("cinque\n", "cinque (PC)\n"))
pc_push()
theirs_text = pc_read("Idee.md")
r = h.handle({"action": "sync"})
assert r["ok"] and r["pushed"] and len(r["copies"]) == 1, r
copy = r["copies"][0]
assert copy.startswith("Idee (conflitto telefono ") and copy.endswith(".md"), copy
pc_pull()
assert pc_read("Idee.md") == theirs_text and pc_read(copy) == ours_text and pc_read("Solo server.md") == "solo qui\n"
print("GitHub cambiato sotto un commit non inviato: versione di GitHub al suo posto, "
      "l'altra in una copia di conflitto, il resto rimesso a posto  OK")

# ── offline: saved on the server, sent later ───────────────────────
run(SERVER, "remote", "set-url", "origin", os.path.join(TMP, "non-esiste.git"))
note = h.handle({"action": "read", "path": "Progetti/Hermes Hub.md"})
r = h.handle({"action": "save", "path": "Progetti/Hermes Hub.md", "text": note["text"] + "Offline.\n", "base": note["sha"]})
assert r["ok"] and not r["pushed"] and r["push_error"], r
assert h.handle({"action": "status"})["ahead"] == 1
run(SERVER, "remote", "set-url", "origin", GITHUB)
r = h.handle({"action": "sync"})
assert r["pushed"] and r["ahead"] == 0 and r["online"], r
pc_pull()
assert pc_read("Progetti/Hermes Hub.md").endswith("Offline.\n")
print("GitHub irraggiungibile: salvato sul server, inviato alla sincronizzazione dopo  OK")

# ── quick capture into the daily note ──────────────────────────────
r = h.handle({"action": "capture", "text": "comprare il latte", "date": "2026-09-26", "time": "09:15"})
assert r["ok"] and r["created"] and r["path"] == "Daily/2026-09-26.md" and r["pushed"], r
r = h.handle({"action": "capture", "text": "chiamare Marco\nper la cena", "date": "2026-09-26", "time": "18:40"})
assert r["ok"] and not r["created"]
daily = server_read("Daily/2026-09-26.md")
assert daily.startswith("# 2026-09-26\n") and "<%" not in daily, daily
assert "## Note libere\n- 09:15 comprare il latte\n- 18:40 chiamare Marco\n  per la cena\n\n\n---\n\n[[Dashboard]]" in daily, daily
r = h.handle({"action": "capture", "text": "in una nota senza sezione", "date": "2026-09-24", "time": "10:00"})
assert server_read("Daily/2026-09-24.md").endswith("Niente sezione per le note.\n\n## Note libere\n- 10:00 in una nota senza sezione\n")
for bad in ({"text": "", "date": "2026-09-26", "time": "09:00"}, {"text": "x", "date": "26/09", "time": "09:00"},
            {"text": "x", "date": "2026-09-26", "time": "9"}):
    assert h.handle({"action": "capture", **bad})["status"] == 400, bad
pc_pull()
assert "chiamare Marco" in pc_read("Daily/2026-09-26.md")
print("nota rapida: giornaliera creata dal modello, righe in fondo a \"Note libere\", arriva sul PC  OK")

# ── attachments ────────────────────────────────────────────────────
png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
up = lambda name, data=png: h.handle({"action": "upload", "name": name, "data": base64.b64encode(data).decode()})  # noqa: E731
r = up("image.png")
assert r["ok"] and r["path"] == "Allegati/image.png" and r["pushed"], r
assert up("image.png")["path"] == "Allegati/image 1.png"
assert up("cartella/sotto/Foto Mare.JPG")["path"] == "Allegati/Foto Mare.jpg"
assert up("../../evil.sh")["status"] == 400 and up("x.svg")["status"] == 400 and up("vuoto.png", b"")["status"] == 400
raw = h.handle({"action": "raw", "path": "Allegati/image.png"})
assert raw["mime"] == "image/png" and base64.b64decode(raw["data"]) == png
assert h.handle({"action": "raw", "path": "Idee.md"})["status"] == 400
pc_pull()
assert os.path.isfile(os.path.join(PC, "Allegati", "image 1.png"))
print("allegati: in Allegati, nomi puliti e mai sovrascritti, solo foto e PDF  OK")

# ── search ─────────────────────────────────────────────────────────
r = h.handle({"action": "search", "q": "perche"})
assert [x["path"] for x in r["results"]] == ["Progetti/Hermes Hub.md"], r
assert r["results"][0]["hits"][0]["text"].startswith("L'Hub serve perché"), r   # not the frontmatter
r = h.handle({"action": "search", "q": "idee"})
assert r["results"][0]["path"] == "Idee.md" and r["results"][0]["in_name"], r
assert h.handle({"action": "search", "q": "x"})["status"] == 400
print("ricerca: senza accenti né maiuscole, prima chi ha la parola nel nome  OK")

# ── an uncommitted change on the server that GitHub also touches ───
with open(os.path.join(SERVER, "Idee.md"), "a", encoding="utf-8", newline="\n") as fh:
    fh.write("scritto da Hermes, non salvato\n")
pc_write("Idee.md", pc_read("Idee.md") + "dal PC\n")
pc_push()
r = h.handle({"action": "capture", "text": "x", "date": "2026-09-26", "time": "20:00"})
assert not r["ok"] and r["status"] == 409 and "Hermes" in r["error"], r
assert server_read("Idee.md").endswith("scritto da Hermes, non salvato\n")
print("modifica non salvata sul server toccata anche da GitHub: fermo, niente toccato  OK")

shutil.rmtree(TMP, ignore_errors=True)
print()
print("OK: vault dal telefono, con git come annulla")
