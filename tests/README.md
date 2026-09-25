# Tests

No framework, no fixtures — scripts that stub Hermes with an `httpx.MockTransport`
and drive the FastAPI app through its test client. They cover the parts that
actually broke or would break silently:

- `test_chat_stream.py` — a turn on a Hermes session: a voice turn gets its
  sentences (text before a tool call included, a spoken cue on an approval), a
  typed turn in the same session gets none and no voice prompt, reasoning never
  reaches the phone, the stream is never compressed, Hermes' errors stay legible.
- `test_runs.py` — a turn picked up again after the connection drops, approvals
  forwarded with their request id and logged with the command, "always" and
  malformed ids refused before reaching Hermes, stop, cross-site approvals refused.
- `test_sessions.py` — session list, search and transcripts reduced to what the
  phone draws: chronological, tool cards with their outcome, no tool output, no
  hidden messages, no session internals.
- `test_audio_proxy.py` — the dashboard proxy: auth header, data-URL handling,
  and that a token mismatch (401) reads differently from a dashboard that is
  down (503).
- `test_auth.py` — the access gate: missing, wrong, tampered, expired and
  unconfigured token all refused; a cookie issued by the old Flask server still
  accepted; cross-site POSTs refused.
- `test_server.py` — the Server tab: overview (services, the others by memory,
  Hermes, 7-day costs), still useful with the dashboard down; restarts only for
  listed units, through the helper, audited, refused cross-site; logs with
  filters and journals of watched units only; cron actions.
- `test_monitor.py` — the alert rules: down for 2 minutes, once, and again on
  recovery; short blips ignored; restarts by systemd with the reason (OOM);
  disk and RAM with hysteresis; failed cron runs once; old news not reported.
- `test_push.py` — Web Push encryption identical to RFC 8291's example, what a
  browser decrypts, VAPID signature and claims, endpoints limited to real push
  services, subscriptions a push service reports gone removed.
- `test_shell.py` — serving the built app: `index.html` never cached, hashed
  assets cached for good, app routes fall back to `index.html`, nothing outside
  `web/dist` reachable.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done
```

`scripts/contract_check.py` is the other half: it runs against the real Hermes
on the server and checks the endpoints these tests stub.
