# Tests

No framework, no fixtures — two scripts that stub the network and drive the Flask
app directly. They cover the parts that actually broke or would break silently:

- `test_chat_stream.py` — SSE framing, incremental sentence cutting, and that
  `event: hermes.tool.progress` never reaches the speaker.
- `test_audio_proxy.py` — the dashboard proxy: auth header, data-URL handling,
  and that a token mismatch (401) reads differently from a dashboard that is
  down (503).

- `test_auth.py` — the access gate: missing, wrong, tampered, expired, and
  unconfigured token all have to be refused.

```bash
pip install flask flask-cors python-dotenv
python tests/test_chat_stream.py
python tests/test_audio_proxy.py
python tests/test_auth.py
```
