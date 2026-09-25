"""Shared setup: environment first, then the app. Import this before anything from hub.

The app is only ever reached over HTTPS (the tunnel terminates TLS), and the
session cookie is Secure: test clients use an https base URL to match.
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

TOKEN = "segreto-di-prova"
TMP = tempfile.mkdtemp(prefix="hub-test-")

os.environ.update({
    "HERMES_API_KEY": "k-agent",
    "HERMES_DASHBOARD_TOKEN": "k-dash",
    "VOICE_AUTH_TOKEN": TOKEN,
    "HUB_DB": os.path.join(TMP, "hub.db"),
    "HUB_WEB_DIST": os.path.join(TMP, "dist"),
    "DISCORD_WEBHOOK_URL": "",
    "DISCORD_BOT_TOKEN": "",
})

import httpx  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from hub import hermes, main, security  # noqa: E402


def anon_client():
    return TestClient(main.app, base_url="https://testserver")


def signed_in_client():
    """A client that already holds a valid session cookie, like the phone."""
    c = TestClient(main.app, base_url="https://testserver")
    c.cookies.set(security.COOKIE, security.cookie_value(time.time() + 3600))
    return c


def mock(handler):
    """Route every outgoing Hermes call to `handler(request) -> httpx.Response`."""
    hermes.transport = httpx.MockTransport(handler)
