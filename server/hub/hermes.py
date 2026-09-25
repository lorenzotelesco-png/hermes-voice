"""Clients for the two Hermes services the hub fronts.

Every call to Hermes goes through here, so that an endpoint changing shape
after `hermes update` is a one-file fix, and scripts/contract_check.py can
test exactly what the hub depends on.
"""
import httpx

from . import config

# Tests swap this for an httpx.MockTransport; production leaves it None.
transport = None


def client(timeout):
    return httpx.AsyncClient(transport=transport, timeout=timeout)


class HermesError(Exception):
    """A call to Hermes failed; `status` is what the hub should answer with."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


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
async def open_chat_stream(messages, session_id=None):
    """Start a streamed completion. Returns (client, response); caller closes both.

    Streamed, so the first sentence can be spoken while the model is still
    writing the rest. Waiting for the complete reply before synthesizing was
    the single largest source of dead air in the voice pipeline.
    """
    headers = {"Authorization": f"Bearer {config.HERMES_API_KEY}"}
    if session_id:
        # Transcript scope: keeps a voice session as one conversation in the
        # dashboard and session history instead of N orphaned turns.
        headers["X-Hermes-Session-Id"] = session_id
        # Stable long-term memory scope — deliberately NOT the session id,
        # which rotates per voice session.
        headers["X-Hermes-Session-Key"] = "hermes-voice:pwa"
    payload = {
        "model": config.HERMES_MODEL,
        "messages": messages,
        "max_tokens": config.HERMES_MAX_TOKENS,
        "stream": True,
    }
    c = client(httpx.Timeout(60, read=180))
    try:
        r = await c.send(c.build_request("POST", config.HERMES_API_URL, json=payload, headers=headers),
                         stream=True)
    except httpx.HTTPError as e:
        await c.aclose()
        raise HermesError(f"Hermes unreachable at {config.HERMES_API_URL} ({e})", status=503) from e
    if r.status_code >= 400:
        body = (await r.aread()).decode(errors="replace")[:300]
        await r.aclose()
        await c.aclose()
        if r.status_code in (401, 403):
            raise HermesError(f"Hermes rejected the request ({r.status_code}). "
                              f"Check HERMES_API_KEY matches API_SERVER_KEY.")
        raise HermesError(f"Hermes HTTP {r.status_code}: {body}")
    return c, r
