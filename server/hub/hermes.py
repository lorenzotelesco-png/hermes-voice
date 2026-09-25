"""Clients for the two Hermes services the hub fronts.

Every call to Hermes goes through here, so that an endpoint changing shape
after `hermes update` is a one-file fix, and scripts/contract_check.py can
test exactly what the hub depends on.
"""
import json
from urllib.parse import quote

import httpx

from . import config

# Tests swap this for an httpx.MockTransport; production leaves it None.
transport = None

# Long-term memory scope for everything said through the hub, voice or text.
# Deliberately not a session id: memory should follow the person across
# conversations. Unchanged from the voice app so its memories carry over.
MEMORY_SCOPE = "hermes-voice:pwa"


def client(timeout):
    return httpx.AsyncClient(transport=transport, timeout=timeout)


class HermesError(Exception):
    """A call to Hermes failed; `status` is what the hub should answer with."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def seg(value):
    """One path segment, so an id can never reach another endpoint."""
    return quote(str(value), safe="")


# ── Dashboard ─────────────────────────────────────────────────────
# Auth uses the dedicated session header rather than Authorization: the
# dashboard prefers it precisely because Authorization collides with reverse
# proxies that do their own basic auth.
def _dashboard_headers():
    return {"X-Hermes-Session-Token": config.DASHBOARD_TOKEN}


def _dashboard_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return HermesError(
                "Dashboard rejected the session token. HERMES_DASHBOARD_TOKEN must match "
                "HERMES_DASHBOARD_SESSION_TOKEN on the dashboard service.")
        return HermesError(f"Dashboard HTTP {code}: {exc.response.text[:300]}")
    return HermesError(
        f"Dashboard unreachable at {config.DASHBOARD_URL} ({exc}). "
        f"Is `hermes dashboard` running?", status=503)


async def dashboard_get(path, timeout=15, params=None):
    try:
        async with client(timeout) as c:
            r = await c.get(config.DASHBOARD_URL + path, headers=_dashboard_headers(), params=params)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise _dashboard_error(e) from e


async def dashboard_post(path, payload, timeout=60):
    try:
        async with client(timeout) as c:
            r = await c.post(config.DASHBOARD_URL + path, headers=_dashboard_headers(), json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise _dashboard_error(e) from e


# ── API server ────────────────────────────────────────────────────
def _api_headers():
    return {"Authorization": f"Bearer {config.HERMES_API_KEY}"}


def _api_status_error(code, text):
    if code in (401, 403):
        return HermesError(f"Hermes rejected the request ({code}). "
                           f"Check HERMES_API_KEY matches API_SERVER_KEY.")
    try:
        message = json.loads(text)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = text[:300]
    # Not found / conflict / bad input are answers about the request, and the
    # phone needs to tell them apart from Hermes being down.
    return HermesError(f"Hermes: {message}", status=code if code in (400, 404, 409) else 502)


def _api_unreachable(exc):
    return HermesError(f"Hermes unreachable at {config.HERMES_API_BASE} ({exc})", status=503)


async def api_request(method, path, payload=None, params=None, timeout=15):
    try:
        async with client(timeout) as c:
            r = await c.request(method, config.HERMES_API_BASE + path, headers=_api_headers(),
                                json=payload, params=params)
    except httpx.HTTPError as e:
        raise _api_unreachable(e) from e
    if r.status_code >= 400:
        raise _api_status_error(r.status_code, r.text)
    return r.json()


async def open_session_stream(session_id, message, system_message=None):
    """Start a turn on a Hermes session. Returns (client, response); caller closes both.

    Hermes keeps the whole conversation server-side, so only the new message
    goes up. Streamed, so the first sentence can be spoken while the model is
    still writing the rest.
    """
    headers = {**_api_headers(), "X-Hermes-Session-Key": MEMORY_SCOPE}
    payload = {"message": message}
    if system_message:
        # Per turn, never stored: a voice turn asks for speakable prose, a
        # typed one in the same conversation may use Markdown.
        payload["system_message"] = system_message
    # No read timeout worth the name: a turn can wait on an approval or a long
    # tool, and Hermes sends keepalives meanwhile.
    c = client(httpx.Timeout(60, read=600))
    try:
        r = await c.send(c.build_request(
            "POST", f"{config.HERMES_API_BASE}/api/sessions/{seg(session_id)}/chat/stream",
            json=payload, headers=headers), stream=True)
    except httpx.HTTPError as e:
        await c.aclose()
        raise _api_unreachable(e) from e
    if r.status_code >= 400:
        text = (await r.aread()).decode(errors="replace")
        await r.aclose()
        await c.aclose()
        raise _api_status_error(r.status_code, text)
    return c, r


async def sse_events(response):
    """(event name, payload) for each event of a Hermes SSE response."""
    event, data = "", []
    async for line in response.aiter_lines():
        if not line:
            if data:
                try:
                    yield event or "message", json.loads("\n".join(data))
                except ValueError:
                    pass
            event, data = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
