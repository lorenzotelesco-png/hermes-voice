"""What systemd and the kernel say about the server, read without privileges.

`systemctl show` and /proc are readable by any user, so this runs inside the
hub as-is; only changing something (a restart) or reading another service's
journal goes through the root-side helper (control.py).
"""
import asyncio
import os
import shutil
import time

# Friendly names for the services the app knows; anything else shows its unit name.
LABELS = {
    "hermes-agent": "Hermes",
    "hermes-dashboard": "Dashboard",
    "hermes-hub": "Hub (questa app)",
    "ngrok-tunnel": "Tunnel ngrok",
    "hermes-webui": "Web UI",
    "tailscaled": "Tailscale",
    "warp-svc": "WARP",
    "warp-socks-ts": "Proxy WARP (Fénix)",
}

_PROPS = "Id,Description,LoadState,ActiveState,SubState,Result,NRestarts,ActiveEnterTimestampMonotonic,MemoryCurrent"
_UNSET = {"", "[not set]", "18446744073709551615"}


class SystemReadError(Exception):
    pass


async def _run(*args, timeout=10):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise SystemReadError(f"{args[0]} did not answer in {timeout}s")
    if proc.returncode != 0:
        raise SystemReadError(f"{' '.join(args[:2])}: {err.decode(errors='replace').strip()[:200]}")
    return out.decode(errors="replace")


def _int(value):
    return None if value in _UNSET else int(value)


def parse_show(text, now_us=None):
    """`systemctl show` output for several units: blocks of Key=Value, blank-line separated."""
    # systemd's monotonic timestamps and Python's time.monotonic() read the
    # same clock (CLOCK_MONOTONIC) on Linux.
    if now_us is None:
        now_us = time.monotonic() * 1e6
    units = []
    for block in text.strip().split("\n\n"):
        p = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not p.get("Id"):
            continue
        name = p["Id"].removesuffix(".service")
        entered = _int(p.get("ActiveEnterTimestampMonotonic", ""))
        active = p.get("ActiveState") == "active"
        units.append({
            "unit": name,
            "label": LABELS.get(name, name),
            "description": p.get("Description", ""),
            "loaded": p.get("LoadState") == "loaded",
            "state": p.get("ActiveState", "unknown"),
            "sub": p.get("SubState", ""),
            "result": p.get("Result", ""),
            "restarts": _int(p.get("NRestarts", "")) or 0,
            # Seconds in the current state, only meaningful while active.
            "up_s": round((now_us - entered) / 1e6) if active and entered else None,
            "memory": _int(p.get("MemoryCurrent", "")),
        })
    return units


async def units(names):
    if not names:
        return []
    out = await _run("systemctl", "show", "--no-pager", "-p", _PROPS, *[n + ".service" for n in names])
    return parse_show(out)


async def running_services():
    """Every running service, for the "everything else" list."""
    out = await _run("systemctl", "list-units", "--type=service", "--state=running",
                     "--no-legend", "--plain", "--no-pager")
    return [line.split()[0].removesuffix(".service") for line in out.splitlines() if line.strip()]


def _meminfo():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, rest = line.split(":", 1)
            info[key] = int(rest.split()[0]) * 1024
    return info


def resources():
    mem = _meminfo()
    disk = shutil.disk_usage("/")
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])
    load = os.getloadavg()
    return {
        "memory": {"total": mem["MemTotal"], "available": mem["MemAvailable"],
                   "swap_total": mem.get("SwapTotal", 0), "swap_free": mem.get("SwapFree", 0)},
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free,
                 "percent": round(disk.used / disk.total * 100, 1)},
        "load": [round(x, 2) for x in load],
        "cpus": os.cpu_count() or 1,
        "uptime_s": round(uptime),
    }
