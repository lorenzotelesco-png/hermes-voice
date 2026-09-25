"""The watcher's rules: when to tell the phone, and when to stay quiet."""
import _setup  # noqa: F401 — environment before hub
from hub import system
from hub.monitor import Watch

GB = 2**30


def unit(name, state="active", restarts=0, result="success"):
    return {"unit": name, "label": name, "loaded": True, "state": state, "sub": "", "result": result,
            "restarts": restarts, "up_s": 10, "memory": None}


def res(disk=50.0, avail=2 * GB):
    return {"disk": {"percent": disk, "free": 10 * GB}, "memory": {"available": avail}}


w = Watch()
keys = lambda evs: [e[0] for e in evs]  # noqa: E731

# ── a service down ─────────────────────────────────────────────────
assert w.evaluate([unit("hermes-agent")], res(), [], 0) == []
assert w.evaluate([unit("hermes-agent", "failed")], res(), [], 100) == []
assert w.evaluate([unit("hermes-agent", "failed")], res(), [], 160) == []          # 60 s down
ev = w.evaluate([unit("hermes-agent", "failed", result="exit-code")], res(), [], 221)  # 121 s
assert keys(ev) == ["down:hermes-agent"] and "exit-code" in ev[0][2], ev
assert w.evaluate([unit("hermes-agent", "failed")], res(), [], 300) == []          # said once
ev = w.evaluate([unit("hermes-agent")], res(), [], 330)
assert keys(ev) == ["down:hermes-agent"] and "di nuovo attivo" in ev[0][1], ev
assert w.evaluate([unit("hermes-agent")], res(), [], 360) == []
print("servizio fermo: avviso dopo 2 minuti, una volta, e al ritorno  OK")

# a blip shorter than the window is not news
w.evaluate([unit("hermes-agent", "activating")], res(), [], 400)
assert w.evaluate([unit("hermes-agent")], res(), [], 430) == []
print("interruzione breve: nessun avviso  OK")

# ── restarts by systemd ────────────────────────────────────────────
ev = w.evaluate([unit("hermes-agent", restarts=1, result="oom-kill")], res(), [], 460)
assert keys(ev) == ["restart:hermes-agent"] and "OOM" in ev[0][2], ev
assert w.evaluate([unit("hermes-agent", restarts=1)], res(), [], 490) == []
print("riavvio da systemd: una notifica col motivo (OOM)  OK")

fresh = Watch()
assert fresh.evaluate([unit("x", restarts=7)], res(), [], 0) == [], "old restarts reported at startup"
print("riavvii vecchi all'avvio dell'Hub: silenzio  OK")

# one notice per half hour for occasional restarts
r = Watch()
r.evaluate([unit("x")], res(), [], 0)
assert keys(r.evaluate([unit("x", restarts=1)], res(), [], 30)) == ["restart:x"]
assert r.evaluate([unit("x", restarts=2)], res(), [], 100) == []
assert keys(r.evaluate([unit("x", restarts=3)], res(), [], 2000)) == ["restart:x"]
print("riavvi sporadici: al massimo una notifica ogni 30 minuti  OK")

# a crash loop (found live: a service restarting every 6 s) is one alert
loop = Watch()
loop.evaluate([unit("webui")], res(), [], 0)
ev = loop.evaluate([unit("webui", "activating", restarts=5, result="exit-code")], res(), [], 30)
assert keys(ev) == ["loop:webui"] and "5 volte" in ev[0][2], ev
assert loop.evaluate([unit("webui", "activating", restarts=10)], res(), [], 60) == []
assert loop.evaluate([unit("webui", "active", restarts=12)], res(), [], 90) == []
ev = loop.evaluate([unit("webui", "active", restarts=12)], res(), [], 700)
assert keys(ev) == ["loop:webui"] and "stabile" in ev[0][1], ev
print("ciclo di riavvii: un solo avviso, e uno quando si stabilizza  OK")

# ── disk, with hysteresis ──────────────────────────────────────────
assert keys(w.evaluate([], res(disk=86), None, 500)) == ["disk"]
assert w.evaluate([], res(disk=84), None, 530) == []
assert keys(w.evaluate([], res(disk=82), None, 560)) == ["disk"]
print("disco oltre 85%: avviso; sotto 83%: rientro; in mezzo: silenzio  OK")

# ── RAM, two looks in a row ────────────────────────────────────────
assert w.evaluate([], res(avail=250 * 2**20), None, 600) == []
assert keys(w.evaluate([], res(avail=250 * 2**20), None, 630)) == ["ram"]
assert w.evaluate([], res(avail=350 * 2**20), None, 660) == []
assert keys(w.evaluate([], res(avail=500 * 2**20), None, 690)) == ["ram"]
print("RAM sotto 300 MB due volte di fila: avviso; oltre 400: rientro  OK")

# ── cron ───────────────────────────────────────────────────────────
c = Watch()
old = [{"id": "a1", "name": "Riepilogo", "last_status": "error", "last_run_at": "t1", "last_error": "vecchio"}]
assert c.evaluate([], None, old, 0) == [], "an old cron failure reported at startup"
new = [{"id": "a1", "name": "Riepilogo", "last_status": "error", "last_run_at": "t2", "last_error": "timeout API"}]
ev = c.evaluate([], None, new, 30)
assert keys(ev) == ["cron:a1"] and "Riepilogo" in ev[0][1] and "timeout API" in ev[0][2], ev
assert c.evaluate([], None, new, 60) == []
ok = [{"id": "a1", "name": "Riepilogo", "last_status": "ok", "last_run_at": "t3"}]
assert c.evaluate([], None, ok, 90) == []
print("cron fallito: una notifica per esecuzione, niente per quelle vecchie  OK")

# ── reading systemctl ──────────────────────────────────────────────
SHOW = """Id=hermes-agent.service
Description=Hermes Agent Gateway
LoadState=loaded
ActiveState=active
SubState=running
Result=success
NRestarts=2
ActiveEnterTimestampMonotonic=1000000
MemoryCurrent=728170496

Id=ngrok-tunnel.service
Description=ngrok
LoadState=loaded
ActiveState=failed
SubState=failed
Result=exit-code
NRestarts=0
ActiveEnterTimestampMonotonic=0
MemoryCurrent=[not set]
"""
u = system.parse_show(SHOW, now_us=61_000_000)
assert u[0]["unit"] == "hermes-agent" and u[0]["label"] == "Hermes" and u[0]["up_s"] == 60, u[0]
assert u[0]["restarts"] == 2 and u[0]["memory"] == 728170496
assert u[1]["state"] == "failed" and u[1]["up_s"] is None and u[1]["memory"] is None, u[1]
print("systemctl show: stato, da quanto, memoria, riavvii  OK")

print()
print("OK: le regole del sorvegliante")
