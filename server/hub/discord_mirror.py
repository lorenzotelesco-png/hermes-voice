"""Optional copy of each voice turn into a Discord thread.

Fire-and-forget: it runs in a worker thread and never affects the turn.
"""
import asyncio
import json
import urllib.request

from . import config

DISCORD_UA = "DiscordBot (https://github.com/lorenzotelesco-png/hermes-voice, 1.0)"

# Per-session thread state, keyed by session id.
_states = {}
# Strong references, or a pending task can be garbage-collected mid-flight.
_tasks = set()


def enabled():
    return bool(config.DISCORD_WEBHOOK and config.DISCORD_TOKEN)


def _post(payload, url):
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_UA,
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[DISCORD] {e}")
        return None


def _bot(path, payload):
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10{path}", data=json.dumps(payload).encode(), headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {config.DISCORD_TOKEN}",
                "User-Agent": DISCORD_UA,
            })
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[DISCORD BOT] {e}")
        return None


def _mirror(user_text, reply, state):
    try:
        if not state.get("thread_id"):
            msg = _post({"content": "🎙️ **Sessione vocale**", "username": "Hermes Voice"},
                        config.DISCORD_WEBHOOK + "?wait=true")
            if not msg:
                return
            thread = _bot(f"/channels/{msg['channel_id']}/messages/{msg['id']}/threads",
                          {"name": "Conversazione vocale", "auto_archive_duration": 60})
            if not thread:
                return
            state["thread_id"] = thread["id"]
        url = config.DISCORD_WEBHOOK + f"?wait=true&thread_id={state['thread_id']}"
        _post({"content": f"🎤 {user_text}", "username": "Lorenzo"}, url)
        _post({"content": reply, "username": "Hermes"}, url)
    except Exception as e:
        print(f"[MIRROR] {e}")


def mirror(user_text, reply, session_id):
    if not enabled():
        return
    state = _states.setdefault(session_id or "default", {})
    task = asyncio.get_running_loop().create_task(asyncio.to_thread(_mirror, user_text, reply, state))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
