"""Hermes turns that outlive the phone's connection.

iOS drops a PWA's connections as soon as it leaves the screen. If the hub
relayed Hermes' stream straight to the phone, that drop would travel upstream
and Hermes would interrupt the turn: a task left running while checking
another app would be killed, and an approval would never be seen.

So the hub reads Hermes' stream itself, into a Run that keeps every event.
The phone follows a Run, and when it comes back it picks up from the last
event it saw. A finished Run is kept a few minutes for that, then dropped.
"""
import asyncio

from . import hermes
from .speech import clean_for_tts, take_sentence

KEEP_FINISHED_S = 600

# Spoken when a voice turn stops on an approval: the request itself is on the
# screen, and reading a shell command aloud helps nobody.
APPROVAL_CUE = "Mi serve una conferma: guarda il telefono."

_runs = {}
_tasks = set()


class Run:
    def __init__(self, session_id, voice):
        self.id = None              # Hermes' run id, known from its first event
        self.session_id = session_id
        self.voice = voice
        self.events = []
        self.finished = False
        self._changed = asyncio.Event()

    def push(self, kind, **fields):
        self.events.append({"seq": len(self.events), "type": kind, **fields})
        self._notify()

    def _notify(self):
        self._changed.set()
        self._changed = asyncio.Event()

    async def follow(self, after=-1, keepalive=15):
        """Batches of events after `after`, until the run ends. [] means "still alive"."""
        i = after + 1
        while True:
            if i < len(self.events):
                batch = self.events[i:]
                i += len(batch)
                yield batch
                continue
            if self.finished:
                return
            try:
                await asyncio.wait_for(self._changed.wait(), keepalive)
            except asyncio.TimeoutError:
                yield []


def get(run_id):
    return _runs.get(run_id)


async def start(session_id, message, voice, system_message=None):
    """Open the turn on Hermes and start reading it. Raises HermesError before any streaming."""
    c, upstream = await hermes.open_session_stream(session_id, message, system_message)
    run = Run(session_id, voice)
    run.push("session", session_id=session_id)
    task = asyncio.create_task(_pump(run, c, upstream))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run


TOOL_STATES = {"tool.started": "running", "tool.completed": "done", "tool.failed": "error"}


async def _pump(run, c, upstream):
    speech = _Speech(run) if run.voice else None
    reply, status = "", "failed"
    try:
        async for name, data in hermes.sse_events(upstream):
            if name == "run.started":
                run.id = data.get("run_id")
                if run.id:
                    _runs[run.id] = run
                    run.push("run", run_id=run.id)
            elif name == "assistant.delta":
                _text(run, speech, data.get("delta") or "")
            elif name == "assistant.commentary" and not data.get("already_streamed"):
                _text(run, speech, (data.get("text") or "") + "\n\n")
            elif name in TOOL_STATES:
                if speech:
                    # The model stopped writing to act: whatever it said
                    # first ("Controllo subito") should be heard now.
                    speech.flush()
                run.push("tool", name=data.get("tool_name") or "tool",
                         preview=data.get("preview") or "", state=TOOL_STATES[name])
            elif name == "approval.request":
                if speech:
                    speech.flush()
                    run.push("sentence", text=APPROVAL_CUE)
                run.push("approval", run_id=data.get("run_id") or run.id,
                         request_id=data.get("request_id"), command=data.get("command") or "",
                         description=data.get("description") or "",
                         choices=data.get("choices") or ["once", "deny"])
            elif name == "assistant.completed":
                reply = data.get("content") or reply
            elif name in ("run.completed", "run.failed", "run.cancelled"):
                status = name[4:]
            elif name == "error":
                run.push("error", message=data.get("message") or "Hermes error")
            elif name == "done":
                break
        if speech:
            speech.flush()
        run.push("done", status=status, reply=reply)
    except Exception as e:  # noqa: BLE001 — whatever broke, the phone must hear about it
        print(f"[RUN ERROR] {run.id}: {e}")
        run.push("error", message=str(e))
        run.push("done", status="failed", reply=reply)
    finally:
        await upstream.aclose()
        await c.aclose()
        run.finished = True
        run._notify()
        if run.id:
            asyncio.get_running_loop().call_later(KEEP_FINISHED_S, _runs.pop, run.id, None)


def _text(run, speech, delta):
    if not delta:
        return
    run.push("delta", text=delta)
    if speech:
        speech.feed(delta)


class _Speech:
    """Cuts a voice turn's text into sentences as it streams, for the phone to speak."""

    def __init__(self, run):
        self.run, self.buf, self.n = run, "", 0

    def feed(self, delta):
        self.buf += delta
        while True:
            sentence, self.buf = take_sentence(self.buf, first=(self.n == 0))
            if not sentence:
                return
            self._say(sentence)

    def flush(self):
        # Whatever never reached a sentence boundary is said anyway, or a reply
        # that ends without punctuation is silently dropped.
        tail, self.buf = self.buf, ""
        self._say(tail)

    def _say(self, text):
        spoken = clean_for_tts(text)
        if spoken:
            self.n += 1
            self.run.push("sentence", text=spoken)
