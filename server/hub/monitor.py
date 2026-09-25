"""The watcher: every 30 s, look at the server and tell the phone what went wrong.

Rules (ROADMAP, phase 2):
  - a watched service not active for 2 minutes     → alert, and again when it is back
  - a watched service restarted by systemd itself  → one notice, with the reason (OOM included)
  - disk over 85%                                  → alert, cleared under 83%
  - available RAM under 300 MB twice in a row      → alert, cleared over 400 MB
  - a cron job whose last run failed               → one notice per failed run

An alert is sent once when it starts and once when it ends, never on every
check. The first look after the hub starts only sets the baseline: old
restarts and old cron failures are not news.
"""
import asyncio
import time

from . import config, hermes, push, system

CHECK_EVERY_S = 30
DOWN_AFTER_S = 120
DISK_ALERT, DISK_CLEAR = 85.0, 83.0
RAM_ALERT, RAM_CLEAR = 300 * 2**20, 400 * 2**20


def _mb(n):
    return f"{n / 2**20:.0f} MB"


class Watch:
    """The rules, with no I/O: facts in, notifications out. Tested on its own."""

    def __init__(self):
        self.alerts = {}            # key -> {"title", "body", "since"}
        self.down_since = {}        # unit -> first time seen not active
        self.restarts = None        # unit -> NRestarts at the last look
        self.cron_seen = None       # job id -> last_run_at already reported
        self.low_ram = 0

    def _raise(self, key, title, body, now, out):
        if key not in self.alerts:
            self.alerts[key] = {"key": key, "title": title, "body": body, "since": now}
            out.append((key, title, body))

    def _clear(self, key, title, body, out):
        if self.alerts.pop(key, None):
            out.append((key, title, body))

    def evaluate(self, units, res, cron, now):
        out = []
        first = self.restarts is None

        for u in units:
            name, label = u["unit"], u["label"]
            if not u["loaded"]:
                continue
            if u["state"] == "active":
                self.down_since.pop(name, None)
                self._clear(f"down:{name}", f"{label} è di nuovo attivo", f"{name} è ripartito.", out)
            else:
                since = self.down_since.setdefault(name, now)
                if now - since >= DOWN_AFTER_S:
                    minutes = int((now - since) // 60)
                    self._raise(f"down:{name}", f"{label} è fermo",
                                f"{name} è {u['state']} da {minutes} minuti"
                                + (f" ({u['result']})" if u["result"] not in ("", "success") else "") + ".",
                                now, out)

        counts = {u["unit"]: u["restarts"] for u in units}
        if not first:
            for u in units:
                before = self.restarts.get(u["unit"])
                if before is not None and u["restarts"] > before:
                    why = "memoria esaurita (OOM)" if u["result"] == "oom-kill" else (u["result"] or "sconosciuto")
                    out.append((f"restart:{u['unit']}", f"{u['label']} si è riavviato",
                                f"systemd ha riavviato {u['unit']} da solo. Motivo: {why}."))
        self.restarts = counts

        if res:
            disk = res["disk"]["percent"]
            if disk >= DISK_ALERT:
                self._raise("disk", "Disco quasi pieno",
                            f"Il disco è al {disk:.0f}%: restano {res['disk']['free'] / 2**30:.1f} GB.", now, out)
            elif disk < DISK_CLEAR:
                self._clear("disk", "Disco di nuovo a posto", f"Il disco è sceso al {disk:.0f}%.", out)

            avail = res["memory"]["available"]
            self.low_ram = self.low_ram + 1 if avail < RAM_ALERT else 0
            if self.low_ram >= 2:
                self._raise("ram", "Memoria quasi finita",
                            f"Restano {_mb(avail)} di RAM disponibile.", now, out)
            elif avail > RAM_CLEAR:
                self._clear("ram", "Memoria di nuovo a posto", f"RAM disponibile: {_mb(avail)}.", out)

        if cron is not None:
            if self.cron_seen is None:
                self.cron_seen = {j.get("id"): j.get("last_run_at") for j in cron}
            else:
                for j in cron:
                    last = j.get("last_run_at")
                    if j.get("last_status") == "error" and last and self.cron_seen.get(j.get("id")) != last:
                        out.append((f"cron:{j.get('id')}", f"Cron fallito: {j.get('name') or j.get('id')}",
                                    (j.get("last_error") or "errore senza messaggio")[:180]))
                    self.cron_seen[j.get("id")] = last
        return out


class Monitor:
    def __init__(self):
        self.watch = Watch()
        self.checked_at = None
        self.last_error = None

    @property
    def alerts(self):
        return sorted(self.watch.alerts.values(), key=lambda a: a["since"])

    async def check(self):
        units = await system.units(config.MONITORED)
        res = system.resources()
        try:
            cron = await hermes.dashboard_get("/api/cron/jobs")
        except hermes.HermesError:
            cron = None   # the dashboard being down is the "down" rule's business
        events = self.watch.evaluate(units, res, cron, time.time())
        self.checked_at = time.time()
        for key, title, body in events:
            print(f"[ALERT] {key}: {title} — {body}")
            try:
                await push.send_all(title, body, tag=key)
            except Exception as e:  # noqa: BLE001
                print(f"[ALERT] push failed: {e}")
        return events

    async def run(self):
        while True:
            try:
                await self.check()
                self.last_error = None
            except Exception as e:  # noqa: BLE001 — the watcher must outlive any one bad look
                self.last_error = str(e)
                print(f"[MONITOR] {e}")
            await asyncio.sleep(CHECK_EVERY_S)


monitor = Monitor()
