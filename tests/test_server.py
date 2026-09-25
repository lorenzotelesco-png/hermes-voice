"""The Server tab's routes: overview, restarts, logs, cron."""
import time

import _setup
import httpx
from hub import audit, control, main, system

GB = 2**30


def u(name, state="active", memory=None):
    return {"unit": name, "label": system.LABELS.get(name, name), "description": "", "loaded": True,
            "state": state, "sub": "running", "result": "success", "restarts": 0, "up_s": 3600, "memory": memory}


async def fake_units(names):
    return [u(n, memory=(2 * GB if n == "nume-web" else 50 * 2**20)) for n in names]


async def fake_running():
    return ["hermes-agent", "nume-web", "nginx", "hermes-hub"]


system.units = fake_units
system.running_services = fake_running
system.resources = lambda: {"memory": {"total": 6 * GB, "available": 2 * GB, "swap_total": 0, "swap_free": 0},
                            "disk": {"total": 60 * GB, "used": 44 * GB, "free": 16 * GB, "percent": 73.4},
                            "load": [0.6, 0.6, 0.5], "cpus": 3, "uptime_s": 149552}

today = time.strftime("%Y-%m-%d", time.gmtime())
dash_calls = []


def dashboard(req):
    dash_calls.append((req.method, req.url.path, dict(req.url.params)))
    p = req.url.path
    if p == "/api/status":
        return httpx.Response(200, json={"version": "0.21.5", "gateway_state": "running", "active_sessions": 1,
                                         "gateway_platforms": {"discord": {"state": "connected"}}})
    if p == "/api/analytics/usage":
        return httpx.Response(200, json={"daily": [{"day": today, "estimated_cost": 0.0123, "api_calls": 9}],
                                         "totals": {"total_estimated_cost": 0.0538, "total_api_calls": 135,
                                                    "total_sessions": 42}})
    if p == "/api/logs":
        return httpx.Response(200, json={"file": "agent", "lines": ["2026 INFO uno", "2026 ERROR due"]})
    if p == "/api/cron/jobs":
        return httpx.Response(200, json=[{"id": "abc123def456", "name": "Riepilogo", "enabled": True,
                                          "state": "scheduled", "schedule": {"kind": "cron", "display": "ogni giorno alle 8"},
                                          "next_run_at": "2026-09-26T08:00:00", "last_status": "error",
                                          "last_error": "x" * 900}])
    if req.method == "POST" and p.startswith("/api/cron/jobs/"):
        return httpx.Response(200, json={"ok": True})
    raise AssertionError(f"unexpected {req.method} {req.url}")


_setup.mock(dashboard)
with _setup.signed_in_client() as c:
    o = c.get("/api/server/overview").json()
    names = [s["unit"] for s in o["services"]]
    assert names[0] == "hermes-agent" and "warp-socks-ts" in names, names
    flags = {s["unit"]: s["restartable"] for s in o["services"]}
    assert flags["hermes-agent"] and not flags["hermes-hub"] and not flags["tailscaled"], flags
    assert [x["unit"] for x in o["others"]] == ["nume-web", "nginx"], o["others"]
    assert o["hermes"] == {"version": "0.21.5", "gateway": "running", "sessions": 1,
                           "platforms": [{"name": "discord", "state": "connected"}]}, o["hermes"]
    assert len(o["usage"]["days"]) == 7 and o["usage"]["today"] == 0.0123 and o["usage"]["week"] == 0.0538
    assert o["resources"]["disk"]["percent"] == 73.4 and not o["errors"], o["errors"]
    print("panoramica: servizi, altri per memoria, Hermes, costi 7 giorni, risorse  OK")

# Dashboard down: the page still shows what the hub can see by itself.
_setup.mock(lambda req: httpx.Response(503, text="down"))
with _setup.signed_in_client() as c:
    o = c.get("/api/server/overview").json()
    assert o["services"] and o["resources"] and o["hermes"] is None and "hermes" in o["errors"], o
print("dashboard giù: servizi e risorse restano, l'errore è detto  OK")

# ── restart ────────────────────────────────────────────────────────
asked = []


async def fake_control(action, unit, **kw):
    asked.append((action, unit, kw))
    if action == "journal":
        return {"ok": True, "lines": ["10:00 avvio", "10:01 errore di rete", "10:02 ok"]}
    return {"ok": True}


_setup.mock(dashboard)
with _setup.signed_in_client() as c:
    r = c.post("/api/server/services/hermes-hub/restart")
    assert r.status_code == 403 and not asked, r.text
    r = c.post("/api/server/services/hermes-agent/restart")
    assert r.status_code == 503 and "deploy" in r.json()["error"], r.text   # helper not installed here
    print("riavvio: fuori lista 403, helper assente 503 con istruzioni  OK")

    main.control.request = fake_control
    assert c.post("/api/server/services/hermes-agent/restart").json() == {"ok": True}
    assert asked == [("restart", "hermes-agent", {})]
    assert audit.recent()[0]["action"] == "restart" and "hermes-agent" in audit.recent()[0]["detail"]
    r = c.post("/api/server/services/hermes-agent/restart", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    print("riavvio: chiesto all'helper, nel registro; da un altro sito 403  OK")

    # ── logs ───────────────────────────────────────────────────────
    r = c.get("/api/server/logs?source=agent&level=error&search=rete&lines=5000").json()
    assert r["lines"] == ["2026 INFO uno", "2026 ERROR due"]
    params = dash_calls[-1][2]
    assert params == {"file": "agent", "lines": "500", "level": "ERROR", "search": "rete"}, params
    r = c.get("/api/server/logs?source=unit:hermes-hub&search=rete").json()
    assert r["lines"] == ["10:01 errore di rete"] and asked[-1] == ("journal", "hermes-hub", {"lines": 200})
    assert c.get("/api/server/logs?source=unit:sshd").status_code == 400
    assert c.get("/api/server/logs?source=agent&level=boh").status_code == 400
    print("log: file di Hermes con filtri, journal dei servizi sorvegliati, altro rifiutato  OK")

    # ── cron ───────────────────────────────────────────────────────
    j = c.get("/api/server/cron").json()["jobs"][0]
    assert j["schedule"] == "ogni giorno alle 8" and len(j["last_error"]) == 300, j
    assert c.post("/api/server/cron/abc123def456/pause").json() == {"ok": True}
    assert dash_calls[-1][:2] == ("POST", "/api/cron/jobs/abc123def456/pause")
    assert audit.recent()[0]["action"] == "cron-pause"
    assert c.post("/api/server/cron/abc123def456/delete").status_code == 400
    assert c.post("/api/server/cron/..%2Fx/pause").status_code in (400, 404)
    print("cron: elenco, pausa inoltrata e registrata, azioni fuori lista rifiutate  OK")

print()
print("OK: panoramica, riavvii, log e cron")
