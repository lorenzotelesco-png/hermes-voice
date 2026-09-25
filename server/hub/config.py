"""Settings, all from the environment.

Read at import time, but kept as module attributes on purpose: the security
gate and the clients look them up per request, so a test can flip one and see
the effect without rebuilding the app.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parents[2]

# ── Access ────────────────────────────────────────────────────────
# Shared secret for the tunnel URL (see security.py). Unset means nobody gets
# in: a gate that opens when misconfigured looks protected and is not.
#   openssl rand -hex 32
VOICE_AUTH_TOKEN = os.environ.get("VOICE_AUTH_TOKEN", "")

# Audit log and, from phase 2 on, push subscriptions and preferences.
HUB_DB = os.environ.get("HUB_DB", str(ROOT / "hub.db"))

# Built frontend (web/ → npm run build).
WEB_DIST = Path(os.environ.get("HUB_WEB_DIST", str(ROOT / "web" / "dist")))

# ── Hermes Agent API server ───────────────────────────────────────
# Older deployments set the full chat completions URL here; only the origin is
# used now (sessions, runs), so both forms work.
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
HERMES_API_BASE = HERMES_API_URL.split("/v1/")[0].rstrip("/")
# Required on every deployment, loopback included. Must equal API_SERVER_KEY
# in ~/.hermes/.env.
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "")

# ── Hermes dashboard (speech in/out, search) ──────────────────────
DASHBOARD_URL = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119")
# Must equal HERMES_DASHBOARD_SESSION_TOKEN on the dashboard service; left
# unset there, the dashboard mints a random one per start and every call 401s.
DASHBOARD_TOKEN = os.environ.get("HERMES_DASHBOARD_TOKEN", "")


def warnings():
    """Misconfigurations worth a line in the journal at startup."""
    out = []
    if not VOICE_AUTH_TOKEN:
        out.append("VOICE_AUTH_TOKEN is not set: every request will be refused with 503.")
    if not DASHBOARD_TOKEN:
        out.append("HERMES_DASHBOARD_TOKEN is not set: speech and dashboard calls will 401.")
    if not HERMES_API_KEY:
        out.append("HERMES_API_KEY is not set: Hermes will reject every chat with 401.")
    return out
