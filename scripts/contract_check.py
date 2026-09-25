#!/usr/bin/env python3
"""Check that every Hermes endpoint the hub relies on still answers in the shape it expects.

Run it on the server before and after every `hermes update`:

    python3 scripts/contract_check.py            # reads .env next to the repo root
    python3 scripts/contract_check.py --chat     # also runs one tiny real turn

Hermes moves fast (11k+ commits in three weeks is normal). The hub talks to
it through a handful of endpoints; this is the list, with the fields each
phase reads. A FAIL names the endpoint and the missing field, which is the
whole diagnosis: fix the adapter in server/hub/hermes.py, or roll back.

Stdlib only, so it runs with the system python3 whatever state the venvs are in.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path):
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(ROOT / ".env")
API_BASE = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642").split("/v1/")[0].rstrip("/")
API_KEY = os.environ.get("HERMES_API_KEY", "")
DASH = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119")
DASH_TOKEN = os.environ.get("HERMES_DASHBOARD_TOKEN", "")
VAULT = os.environ.get("HUB_VAULT_PATH", "/root/obsidian-vault")

# (service, path, fields that must exist — dotted, "[]" for "is a list", phase that needs it)
CHECKS = [
    ("api", "/health", ["status"], 0),
    ("api", "/v1/models", ["data"], 0),
    ("api", "/v1/capabilities", ["features.chat_completions_streaming", "features.tool_progress_events"], 0),
    ("dash", "/api/audio/voice-config", ["stt.mode"], 0),
    ("api", "/v1/capabilities", ["features.session_chat_streaming", "features.run_approval_response",
                                 "features.run_stop"], 1),
    ("api", "/api/sessions?limit=1", ["data", "has_more"], 1),
    ("dash", "/api/sessions/search?q=hermes", ["results"], 1),
    ("dash", "/api/status", ["version", "gateway_running", "gateway_platforms"], 2),
    ("dash", "/api/system/stats", ["memory.available", "disk.free", "cpu_percent", "load_avg"], 2),
    ("dash", "/api/logs?file=agent&lines=1", ["lines"], 2),
    ("dash", "/api/analytics/usage?days=1", ["daily", "by_model"], 2),
    ("dash", "/api/cron/jobs", ["[]"], 2),
    ("dash", f"/api/fs/list?path={VAULT}", ["entries"], 3),
]


def get(url, headers, body=None):
    req = urllib.request.Request(url, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        return None, str(e).encode()


def has(value, dotted):
    if dotted == "[]":
        return isinstance(value, list)
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def main():
    headers = {"api": {"Authorization": f"Bearer {API_KEY}"},
               "dash": {"X-Hermes-Session-Token": DASH_TOKEN}}
    base = {"api": API_BASE, "dash": DASH}
    failed = 0
    for service, path, fields, phase in CHECKS:
        status, raw = get(base[service] + path, headers[service])
        problem = None
        if status != 200:
            problem = f"HTTP {status}: {raw[:120].decode(errors='replace')}"
        else:
            try:
                data = json.loads(raw)
                missing = [f for f in fields if not has(data, f)]
                if missing:
                    problem = "missing " + ", ".join(missing)
            except ValueError:
                problem = "not JSON"
        failed += bool(problem)
        print(f"{'FAIL' if problem else 'ok  '}  fase {phase}  {service:4} {path}" + (f"  -> {problem}" if problem else ""))

    if "--chat" in sys.argv:
        # One real turn on the session endpoint the hub uses, in a throwaway session.
        api = {**headers["api"], "Content-Type": "application/json"}
        status, raw = get(API_BASE + "/api/sessions", api, json.dumps({"title": "contract check"}).encode())
        sid = json.loads(raw)["session"]["id"] if status == 201 else None
        ok = False
        if sid:
            body = json.dumps({"message": "Rispondi solo con la parola: pronto"}).encode()
            status, raw = get(f"{API_BASE}/api/sessions/{sid}/chat/stream", api, body)
            ok = status == 200 and b"event: assistant.delta" in raw and b"event: run.completed" in raw
            req = urllib.request.Request(f"{API_BASE}/api/sessions/{sid}", headers=api, method="DELETE")
            try:
                urllib.request.urlopen(req, timeout=30).close()
            except OSError:
                pass
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  fase 1  api  POST /api/sessions/{{id}}/chat/stream"
              + ("" if ok else f"  -> HTTP {status}"))

    print()
    print("tutto ok" if not failed else f"{failed} controlli falliti")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
