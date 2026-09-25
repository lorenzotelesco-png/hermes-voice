"""Turning a streamed reply into speakable chunks."""
import json
import re

# A sentence ends at .!? only when what follows is not a lowercase letter, so
# "Dr. Rossi" and "es. questo" stay whole.
_SENTENCE_END = re.compile(r'[.!?](?=\s+[^a-z\s]|\s*$)')

# Below this, a "sentence" is a fragment ("Ok.") and synthesizing it on its own
# costs a round trip for a word. Hermes' own speaker pipeline uses the same floor.
_MIN_SENTENCE_CHARS = 20

# The FIRST fragment is allowed to be much shorter. A reply that opens with
# "Certo!" (6 chars) would otherwise be held back until the next sentence
# completed — measured on a phone, that is the difference between hearing
# something at 5s and hearing nothing until 8.5s. Paying a synthesis round trip
# for one word is worth it exactly once, at the start, where all the silence is.
_MIN_FIRST_CHARS = 4

# For the opening chunk only, a clause boundary is good enough. A reply that
# starts "Certo, il meteo a Milano oggi e sereno con una massima di ventidue
# gradi." is ONE sentence: waiting for its full stop holds every bit of audio
# hostage behind the whole thing. Cutting at the comma makes "Certo," speakable
# immediately. Later chunks keep the full-stop rule — they are synthesized while
# the previous one plays, so there is nothing to gain and prosody to lose.
_CLAUSE_END = re.compile(r'[,;:](?=\s)')


def take_sentence(buf, first=False):
    """Split off the first speakable chunk. Returns (chunk|None, remainder)."""
    if first:
        for pattern in (_SENTENCE_END, _CLAUSE_END):
            for m in pattern.finditer(buf):
                if m.end() >= _MIN_FIRST_CHARS:
                    return buf[:m.end()].strip(), buf[m.end():].lstrip()
        return None, buf
    for m in _SENTENCE_END.finditer(buf):
        if m.end() >= _MIN_SENTENCE_CHARS:
            return buf[:m.end()].strip(), buf[m.end():].lstrip()
    return None, buf


def clean_for_tts(text):
    text = re.sub(r'<@!?\d+>', '', text)
    text = re.sub(r'<#\d+>', '', text)
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
    text = re.sub(r'#+\s', '', text)
    text = re.sub(r'\n{2,}', ' ', text)
    return text.strip()


# ngrok's free tier would not reliably flush a small chunk: the same payload
# streamed correctly one run and arrived as one 42ms burst the next. Every event
# was padded past the buffer threshold with an SSE comment (ignored by every
# parser). tailscale serve should flush event streams on its own; the padding
# stays until a timed comparison on the phone shows it is safe to drop.
_FLUSH_PAD = ": " + (" " * 2048) + "\n"


def sse(payload):
    return _FLUSH_PAD + "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
