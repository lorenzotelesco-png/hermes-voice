"""Hermes sessions and messages, reduced to what the phone shows.

Tool outputs stay on the server: a directory listing or a web page can be
hundreds of kilobytes, and the thread only needs to say which tool ran and
whether it worked.
"""
import json

PREVIEW_CHARS = 140

# The argument that says what a call was about, in the order worth showing.
_PREVIEW_KEYS = ("command", "query", "url", "path", "file_path", "pattern", "name", "prompt", "text")


def session(s):
    return {key: s.get(key) for key in (
        "id", "source", "title", "preview", "started_at", "last_active", "ended_at", "message_count")}


def search_hit(h):
    return {
        "session_id": h.get("session_id") or h.get("id"),
        "title": h.get("title"),
        "source": h.get("source"),
        "snippet": (h.get("snippet") or "").strip(),
        "last_active": h.get("last_active"),
    }


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):   # multimodal: keep the words, drop the images
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _short(value):
    value = " ".join(str(value).split())
    return value if len(value) <= PREVIEW_CHARS else value[:PREVIEW_CHARS - 1] + "…"


def _preview(arguments):
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except ValueError:
        return _short(arguments)
    if not isinstance(args, dict):
        return ""
    for key in _PREVIEW_KEYS:
        if isinstance(args.get(key), str) and args[key].strip():
            return _short(args[key])
    first = next((v for v in args.values() if isinstance(v, str) and v.strip()), "")
    return _short(first)


def _failed(content):
    try:
        out = json.loads(content)
    except (TypeError, ValueError):
        return content.lstrip().lower().startswith("error")
    if not isinstance(out, dict):
        return False
    return bool(out.get("error")) or out.get("exit_code") not in (None, 0)


def _order(m):
    mid = m.get("id")
    return (m.get("timestamp") or 0, int(mid) if str(mid).isdigit() else 0)


def items(messages):
    """The thread as the phone draws it: user and assistant text, and one card per tool call."""
    out, calls = [], {}
    for m in sorted(messages, key=_order):
        # Hidden scaffolding, compaction notices, internal notifications.
        if m.get("display_kind"):
            continue
        role, text = m.get("role"), _text(m.get("content")).strip()
        if role == "user" and text:
            out.append({"role": "user", "text": text})
        elif role == "assistant":
            if text:
                out.append({"role": "assistant", "text": text})
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                card = {"role": "tool", "name": fn.get("name") or "tool",
                        "preview": _preview(fn.get("arguments")), "state": "done"}
                calls[call.get("id")] = card
                out.append(card)
        elif role == "tool":
            card = calls.get(m.get("tool_call_id"))
            if card and _failed(m.get("content")):
                card["state"] = "error"
    return out
