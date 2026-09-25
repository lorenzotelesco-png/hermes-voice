# Tests

No framework, no fixtures — scripts that stub Hermes with an `httpx.MockTransport`
and drive the FastAPI app through its test client. They cover the parts that
actually broke or would break silently:

- `test_chat_stream.py` — SSE framing, incremental sentence cutting, and that
  `event: hermes.tool.progress` never reaches the speaker.
- `test_audio_proxy.py` — the dashboard proxy: auth header, data-URL handling,
  and that a token mismatch (401) reads differently from a dashboard that is
  down (503).
- `test_auth.py` — the access gate: missing, wrong, tampered, expired and
  unconfigured token all refused; a cookie issued by the old Flask server still
  accepted; cross-site POSTs refused.
- `test_shell.py` — serving the built app: `index.html` never cached, hashed
  assets cached for good, app routes fall back to `index.html`, nothing outside
  `web/dist` reachable.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done
```

`scripts/contract_check.py` is the other half: it runs against the real Hermes
on the server and checks the endpoints these tests stub.
