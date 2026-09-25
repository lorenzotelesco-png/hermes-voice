"""Serving the built app: caching rules, SPA fallback, no escaping the dist folder."""
import os

import _setup

dist = os.environ["HUB_WEB_DIST"]
c = _setup.signed_in_client()

r = c.get("/")
assert r.status_code == 503 and "npm run build" in r.json()["error"], r.text
print("frontend non compilato: 503 con istruzioni  OK")

os.makedirs(os.path.join(dist, "assets"))
with open(os.path.join(dist, "index.html"), "w") as f:
    f.write("<!doctype html><title>hub</title>")
with open(os.path.join(dist, "assets", "app-abc123.js"), "w") as f:
    f.write("console.log(1)")
with open(os.path.join(dist, "manifest.webmanifest"), "w") as f:
    f.write("{}")

r = c.get("/")
assert r.status_code == 200 and "<title>hub" in r.text
assert "no-store" in r.headers["cache-control"], r.headers["cache-control"]
print("index.html: mai in cache  OK")

r = c.get("/assets/app-abc123.js")
assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
print("asset con hash: cache permanente  OK")

r = c.get("/manifest.webmanifest")
assert r.status_code == 200 and "immutable" not in r.headers["cache-control"]
print("file non versionati: rivalidati  OK")

r = c.get("/server")
assert r.status_code == 200 and "<title>hub" in r.text
print("rotta dell'app: fallback su index.html  OK")

r = c.get("/%2e%2e/%2e%2e/server/hub/config.py")
assert "HUB_ALLOWED" not in r.text
print("path traversal: nessun file fuori da dist  OK")

print()
print("OK: shell servita con le regole di cache giuste")
