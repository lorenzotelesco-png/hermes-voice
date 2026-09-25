"""Who may talk to the hub.

The hub sits behind the public tunnel URL, and the agent it fronts can search
the web, read memory, run tools and spend API credits: an open URL is an open
agent. The gate is the voice app's, unchanged — same token, same cookie name,
same signature — so a phone that is already signed in stays signed in.

Entry is ?k=<token> once; after that a signed cookie carries the session, so
the token does not have to live in the home-screen URL. It fails closed: with
VOICE_AUTH_TOKEN unset every request is refused, because a control that opens
when misconfigured looks protected and is not.

On top of that, state-changing requests must be same-origin. There is no CORS
middleware either: the only client is the app itself.
"""
import hashlib
import hmac
import time
from urllib.parse import urlsplit

from . import config

COOKIE = "hv_auth"
MAX_AGE = 365 * 24 * 3600          # a phone should not re-authenticate often
PUBLIC_PATHS = {"/health", "/api/health",
                # Static files with nothing private in them, which iOS fetches
                # without the cookie: the home-screen icon and name, the push
                # worker. Refusing them only filled the log with 401s and could
                # leave the app without its icon.
                "/favicon.ico", "/manifest.json", "/sw.js",
                "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _sign(raw):
    return hmac.new(config.VOICE_AUTH_TOKEN.encode(), raw.encode(), hashlib.sha256).hexdigest()


def cookie_value(expires_at):
    """expiry + HMAC over it. The token itself never travels in the cookie."""
    raw = str(int(expires_at))
    return raw + "." + _sign(raw)


def cookie_ok(value):
    try:
        raw, sig = (value or "").split(".", 1)
        if int(raw) < time.time():
            return False
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(sig, _sign(raw))


def same_origin(request):
    # Every current browser sends Sec-Fetch-Site; it is the direct answer.
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if not origin:
        return True
    # The tunnel may or may not rewrite Host, so accept either spelling.
    hosts = {request.headers.get("host", ""), request.headers.get("x-forwarded-host", "")}
    return urlsplit(origin).netloc in hosts - {""}


def check(request):
    """None when the request may proceed, else (status, message).

    Sets request.state.grant_cookie when the request carried a valid ?k=.
    """
    if not config.VOICE_AUTH_TOKEN:
        return 503, "VOICE_AUTH_TOKEN is not set on the server"
    if request.url.path in PUBLIC_PATHS:
        return None
    key = request.query_params.get("k", "")
    if key and hmac.compare_digest(key.encode(), config.VOICE_AUTH_TOKEN.encode()):
        request.state.grant_cookie = True
    elif not cookie_ok(request.cookies.get(COOKIE)):
        return 401, "unauthorized"
    if request.method not in SAFE_METHODS and not same_origin(request):
        return 403, "cross-site request refused"
    return None


def grant_cookie(request, response):
    if getattr(request.state, "grant_cookie", False):
        response.set_cookie(
            COOKIE, cookie_value(time.time() + MAX_AGE), max_age=MAX_AGE,
            httponly=True, samesite="lax",
            # The tunnel terminates TLS, so the cookie must never travel plain.
            secure=request.headers.get("x-forwarded-proto", "https") == "https",
        )


# Deliberately says nothing about what runs here: an unauthenticated visitor
# learns only that a token is needed. Submitting reloads with ?k=, which the
# gate exchanges for the cookie.
LOGIN_PAGE = """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Hermes</title>
<style>
 body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
      background:#0a0a0a;color:#e8e0d0;font:16px -apple-system,system-ui,sans-serif}
 form{display:flex;flex-direction:column;gap:14px;width:min(320px,80vw)}
 h1{font:300 15px/1 -apple-system,system-ui;letter-spacing:.3em;text-align:center;
    opacity:.55;margin:0 0 6px;text-transform:uppercase}
 input{padding:13px;border-radius:10px;border:1px solid #2e2e2e;background:#151515;
       color:#e8e0d0;font-size:16px}
 button{padding:13px;border-radius:10px;border:0;background:#e8e0d0;color:#0a0a0a;
        font-size:16px;font-weight:600}
</style>
<form onsubmit="location.search='?k='+encodeURIComponent(t.value);return false">
  <h1>Hermes</h1>
  <input id=t type=password placeholder="token di accesso"
         autocomplete="current-password" autofocus>
  <button>Entra</button>
</form>"""
