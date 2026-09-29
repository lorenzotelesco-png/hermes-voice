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
- `test_vault_helper.py` — the vault helper against three real git repos
  standing in for GitHub, the server and the PC: a save reaches the PC's pull;
  edits to different lines merge; the same line comes back as a conflict with
  nothing written; GitHub moving under an unpushed commit leaves both versions;
  offline saves go out on the next sync; quick notes land under "Note libere";
  uploads never overwrite; paths out of the vault, hidden or through a symlink
  refused; accent-blind search.
- `test_files.py` — the File tab's routes: the helper's refusals keep their
  status (409 with the PC's version), images served with their type, uploads
  over 15 MB stopped in the hub, every write audited; Hermes' folders only under
  their roots, strange paths stopped before the dashboard.
- `test_assistant.py` — Hey Hermes: the Shortcut's key made only from the app,
  shown once, never logged, opening that one path and no other; follow-ups in
  the same session for 10 minutes; goodbyes that never reach Hermes; an
  approval or a long task ending the call with a sentence and a notification
  that opens the app on it; a rate limit; revocation.
- `test_code.py` — the Code tab with a software passkey signing like an
  iPhone: locked until Face ID; wrong origin, no user verification, altered
  signature, unknown key, another site and replayed challenges refused; a
  second passkey only after an unlock; OpenCode's sessions, transcript,
  prompt, stop and permissions (never "always"), events limited to one
  session, terminals and the WebSocket relay, which refuses no login, no
  unlock and other sites.
- `test_inbox.py` — the Inbox against a Beeper answering like its API 5.0:
  Beeper's state read before sign-in, after it (a 401 without a token) and
  when it is down; connecting with PKCE, refused then approved in Beeper's
  window, the token kept with its expiry; accounts, chat list (merged copies
  hidden, avatars only from Beeper's folder), search and unread; a chat in
  order with reactions on their message and hidden ones left out; sends (empty
  and cross-site refused, replies, audited); read; media only from Beeper,
  with Range; a revoked token asking to reconnect. The event watcher: new
  messages to the open app, and a notification only for others' new messages,
  once, not for history, muted chats, a chat already open, or with
  notifications off.
- `test_shell.py` — serving the built app: `index.html` never cached, hashed
  assets cached for good, app routes fall back to `index.html`, nothing outside
  `web/dist` reachable.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done
```

`scripts/contract_check.py` is the other half: it runs against the real Hermes
on the server and checks the endpoints these tests stub.
