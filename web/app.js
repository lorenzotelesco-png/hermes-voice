const API = '';
const NL = String.fromCharCode(10);
const SSE_SEP = NL + NL;
let audioCtx, analyser, stream, recorder, chunks = [];
let isSpeaking = false, isProcessing = false, isActive = false, isMuted = false;
let silenceTimer = null, rafId = null;
let history = [], currentAudio = null, sessionId = null;

function makeSessionId() {
  // Generate a stable UUID for this voice session (used for Discord thread tracking)
  return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
    (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16));
}
let volume = 0;
let noiseFloor = 5;      // calibrated dynamically
let calibrating = true;
let calibSamples = [];

const blobs   = [0,1,2,3].map(i => document.getElementById('b'+i));
const label   = document.getElementById('label');
const errDiv  = document.getElementById('error');

// ── BLOB ANIMATION ────────────────────────────────────────────────
const phases = [0, 0.7, 1.4, 2.1];
let animTime = 0, blobState = 'idle';

function animBlobs(ts) {
  if (!isActive) return;
  animTime = ts / 1000;
  blobs.forEach((b, i) => {
    let sy = 1, sx = 1, op = 1;
    const p = phases[i];
    if (blobState === 'idle') {
      sy = 1 + 0.06 * Math.sin(animTime * 1.2 + p);
      sx = 1 - 0.03 * Math.sin(animTime * 1.2 + p);
      op = 0.85 + 0.1 * Math.sin(animTime + p);
    } else if (blobState === 'listening') {
      const boost = volume * (1 + 0.4 * Math.sin(animTime * 8 + p));
      sy = 1 + 0.12 * Math.sin(animTime * 3.5 + p) + boost * 0.7;
      sx = 1 / (0.95 + sy * 0.05);
      op = 0.7 + 0.3 * Math.min(1, volume * 3 + 0.2);
    } else if (blobState === 'speaking') {
      const wave = Math.sin(animTime * 9 + p * 1.5);
      sy = 1.1 + 0.45 * wave + 0.1 * Math.sin(animTime * 5 + p);
      sx = 1 / (0.92 + sy * 0.08);
      op = 1;
    } else if (blobState === 'thinking') {
      const t = (animTime * 0.6 + i * 0.18) % 1;
      op = 0.2 + 0.75 * Math.pow(Math.sin(t * Math.PI), 2);
      sy = 0.75 + 0.3 * Math.pow(Math.sin(t * Math.PI), 2);
    }
    b.style.transform = `scaleY(${sy.toFixed(3)}) scaleX(${sx.toFixed(3)})`;
    b.style.opacity   = op.toFixed(3);
  });
  rafId = requestAnimationFrame(animBlobs);
}

function setState(s, text) {
  blobState = s;
  document.body.className = s;
  label.textContent = text;
}

function showError(msg, ms = 6000) {
  errDiv.textContent = msg;
  errDiv.style.display = 'block';
  setTimeout(() => errDiv.style.display = 'none', ms);
}

// ── SESSION ───────────────────────────────────────────────────────
async function startSession() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        // Hardware echo cancellation is what makes barge-in possible at all on a
        // phone speaker: without it the mic hears our own reply and every sentence
        // interrupts itself.
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  } catch(e) {
    showError('Microfono non disponibile: ' + e.message, 8000); return;
  }
  document.getElementById('start-screen').style.display = 'none';
  document.getElementById('main-screen').style.display  = 'flex';
  isActive = true;
  sessionId = makeSessionId();   // stable ID for this entire voice session

  // No custom sampleRate — let Safari use its native rate
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') await audioCtx.resume();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.5;
  audioCtx.createMediaStreamSource(stream).connect(analyser);

  calibrating = true; calibSamples = [];
  setState('idle', 'calibrazione...');
  requestAnimationFrame(animBlobs);

  // Calibrate noise floor for 1.5 seconds
  setTimeout(() => {
    noiseFloor = calibSamples.length
      ? calibSamples.reduce((a,b) => a+b, 0) / calibSamples.length
      : 5;
    noiseFloor = Math.max(3, noiseFloor);
    calibrating = false;
    setState('listening', 'in ascolto');
    monitorLoop();
  }, 1500);

  monitorCalib();
}

function monitorCalib() {
  if (!calibrating) return;
  calibSamples.push(getRMS());
  setTimeout(monitorCalib, 80);
}

function stopSession() {
  isActive = false;
  cancelAnimationFrame(rafId);
  clearTimeout(silenceTimer);
  if (recorder && recorder.state !== 'inactive') recorder.stop();
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (audioCtx) audioCtx.close();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  history = []; sessionId = null;
  blobs.forEach(b => { b.style.transform = ''; b.style.opacity = ''; });
  document.getElementById('main-screen').style.display  = 'none';
  document.getElementById('start-screen').style.display = 'flex';
}

function toggleMute() {
  isMuted = !isMuted;
  // Muting mid-utterance means "I am done, take it now". No VAD heuristic is
  // right every time, so there has to be a way to end a turn on purpose —
  // especially for a long pause the detector would otherwise cut into.
  if (isMuted && isSpeaking) {
    clearTimeout(silenceTimer);
    endSpeech();
  }
  stream.getAudioTracks().forEach(t => t.enabled = !isMuted);
  document.getElementById('btn-mute').style.opacity = isMuted ? '0.35' : '1';
}

// ── VAD ───────────────────────────────────────────────────────────
const qs = (k, d) => Number(new URLSearchParams(location.search).get(k)) || d;

// Endpointing: how long a pause must last before the turn is considered over.
// Dead time paid on every turn, so it is tempting to shrink — but cutting the
// speaker off mid-sentence costs a whole retry, which is far worse than 300ms.
const SILENCE_MS = qs('silence', 900);

// Two thresholds, not one. Entering speech has to clear a high bar so room
// noise cannot open a turn; STAYING in speech only has to clear a low one,
// because natural speech constantly dips — between words, on unvoiced
// consonants, in mid-sentence pauses for thought. With a single bar every one
// of those dips looks like the end of the turn.
const START_MULT    = qs('start', 2.8);
const CONTINUE_MULT = qs('cont', 1.35);

// A burst shorter than this is a cough, a door, a keyboard — not a turn.
// Transcribing it wastes a round trip and confuses the conversation.
const MIN_UTTERANCE_MS = qs('minms', 500);

let speechStartedAt = 0;

// ── Strumentazione (?debug=1) ─────────────────────────────────────
// Where the time actually goes, shown on the phone. Guessing by ear cannot
// distinguish "the model is slow" from "the sentences all arrive at once
// because something buffered the stream" — and those need opposite fixes.
const DEBUG = new URLSearchParams(location.search).has('debug');
let mark0 = 0, marks = [];
const T = (label) => {
  if (!DEBUG) return;
  marks.push([label, performance.now() - mark0]);
  const el = document.getElementById('debug');
  if (el) el.textContent = marks.map(([l, ms]) => `${l} ${(ms / 1000).toFixed(2)}`).join('  ');
};

// Barge-in: the mic stays live while Hermes speaks so it can be interrupted.
// The bar is higher than for normal listening because the mic still picks up
// some of our own output even with AEC on, and a short grace period after audio
// starts stops the first syllable of the reply from tripping the detector.
const BARGE_IN_MULT     = 4.0;
const BARGE_IN_GRACE_MS = 500;
let playbackStartedAt = 0;

function getRMS() {
  const d = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteTimeDomainData(d);
  let sum = 0;
  for (let i = 0; i < d.length; i++) { const v = (d[i] - 128) / 128; sum += v * v; }
  return Math.sqrt(sum / d.length) * 100;
}

function startRecording() {
  isSpeaking = true; chunks = []; speechStartedAt = performance.now();
  recorder = new MediaRecorder(stream, { mimeType: getAudioMime() });
  recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
  recorder.start(100);
}

// Cut the reply short. The turn is abandoned, so drop the processing flag right
// away — otherwise the recorder we are about to start would be gated out by it.
function interruptPlayback() {
  ttsInterrupted = true;
  isProcessing = false;
  if (currentAudio) { try { currentAudio.stop(); } catch(_) {} }
}

function monitorLoop() {
  if (!isActive || calibrating) return;
  const rms = getRMS();
  volume = volume * 0.75 + (rms / (noiseFloor * 8)) * 0.25;

  const speaking = blobState === 'speaking';
  const gateOpen = speaking
    ? (performance.now() - playbackStartedAt) > BARGE_IN_GRACE_MS
    : !isProcessing;

  // Already recording? Then the only job is to notice we are still talking, and
  // a much lower bar is enough for that.
  const threshold = noiseFloor * (isSpeaking ? CONTINUE_MULT
                                : speaking   ? BARGE_IN_MULT
                                             : START_MULT);

  if (!isMuted && gateOpen && rms > threshold) {
    if (speaking) interruptPlayback();
    if (!isSpeaking) startRecording();
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(endSpeech, SILENCE_MS);
  }
  setTimeout(monitorLoop, 40);
}

function getAudioMime() {
  const types = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', ''];
  return types.find(t => !t || MediaRecorder.isTypeSupported(t)) || '';
}

function endSpeech() {
  if (!isSpeaking || isProcessing) return;

  // Too short to be a turn: throw it away and keep listening rather than paying
  // a transcription round trip for a cough.
  if (performance.now() - speechStartedAt < MIN_UTTERANCE_MS) {
    isSpeaking = false;
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = () => { chunks = []; };
      recorder.stop();
    }
    if (isActive) setState('listening', 'in ascolto');
    return;
  }

  isSpeaking = false; isProcessing = true;
  mark0 = performance.now(); marks = []; T('fine-voce');
  setState('thinking', 'elaboro...');
  recorder.onstop = async () => {
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    await handleAudio(blob);
    isProcessing = false;
    // A barge-in may already have started the next recording while this turn was
    // unwinding; do not reset the UI out from under it.
    if (isActive && !isSpeaking) { volume = 0; setState('listening', 'in ascolto'); }
  };
  if (recorder.state !== 'inactive') recorder.stop();
}

// ── PIPELINE ──────────────────────────────────────────────────────
let ttsInterrupted = false;

function mimeToExt(mime) {
  if (!mime) return '.webm';
  if (mime.includes('mp4'))  return '.m4a';
  if (mime.includes('ogg'))  return '.ogg';
  if (mime.includes('mpeg')) return '.mp3';
  return '.webm';
}

// Fetch TTS audio for one piece of text and decode it.
async function fetchAndDecodeTTS(text) {
  const res  = await fetch(API + '/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);
  const bytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0)).buffer;
  return audioCtx.decodeAudioData(bytes);
}

// Play a pre-decoded AudioBuffer. Returns a promise that resolves when done.
function playBuffer(decoded) {
  return new Promise(res => {
    const src = audioCtx.createBufferSource();
    src.buffer = decoded;
    src.connect(audioCtx.destination);
    currentAudio = src;

    playbackStartedAt = performance.now();
    const onTap = () => interruptPlayback();
    document.getElementById('main-screen').addEventListener('click', onTap, { once: true });
    src.onended = () => {
      document.getElementById('main-screen').removeEventListener('click', onTap);
      currentAudio = null;
      res();
    };
    src.start(0);
  });
}

async function handleAudio(blob) {
  try {
    const fd  = new FormData();
    const ext = mimeToExt(recorder.mimeType);
    fd.append('audio', blob, 'speech' + ext);

    const sttRes  = await fetch(API + '/transcribe', { method: 'POST', body: fd });
    const sttData = await sttRes.json();
    if (sttData.error) { showError('STT: ' + sttData.error); return; }
    T('stt');
    const text = (sttData.text || '').trim();
    if (text.length < 2) return;   // silent or noise

    history.push({ role: 'user', content: text });

    ttsInterrupted = false;
    const chatRes = await fetch(API + '/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ history: history.slice(-8), session_id: sessionId })
    });
    if (!chatRes.ok || !chatRes.body) {
      const err = await chatRes.json().catch(() => ({}));
      showError('Chat: ' + (err.error || chatRes.status));
      return;
    }

    const spoken = await speakStream(chatRes);
    if (!spoken) return;
    // Record what was actually said, not what was generated. If the user cut in,
    // the agent should see a truncated turn — otherwise it carries on as though
    // the whole reply had landed.
    history.push({
      role: 'assistant',
      content: ttsInterrupted ? spoken + ' [interrotto dall utente]' : spoken,
    });
  } catch(e) {
    showError('Errore: ' + e.message);
    console.error(e);
  }
}

// Parse an SSE body incrementally. EventSource cannot be used because the chat
// call is a POST, so the framing is handled here: events are separated by a
// blank line, and only "data:" lines carry payload.
async function* sseEvents(res) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf(SSE_SEP)) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + SSE_SEP.length);
      for (const line of block.split(NL)) {
        if (!line.startsWith('data:')) continue;
        try { yield JSON.parse(line.slice(5).trim()); } catch (_) {}
      }
    }
  }
}

// Speak the reply as it is written. The server cuts sentences and pushes them
// down the stream; each one starts synthesizing the moment it arrives, so the
// first word is spoken while the model is still writing the rest.
//
// Returns the text actually spoken, which is not the full reply when the user
// barged in — the agent is told what it managed to say, not what it intended.
async function speakStream(res) {
  const queue = [];
  let wake = null, producerDone = false, failed = null;

  const producer = (async () => {
    try {
      for await (const ev of sseEvents(res)) {
        if (ev.error) { failed = ev.error; break; }
        if (ev.done) break;
        if (!ev.sentence) continue;
        T('frase' + (queue.length + 1));
        // Synthesis starts here, not at playback time: sentence N+1 is being
        // fetched while N is still playing.
        queue.push({ text: ev.sentence, audio: fetchAndDecodeTTS(ev.sentence) });
        if (wake) { wake(); wake = null; }
      }
    } catch (e) {
      failed = e.message;
    } finally {
      producerDone = true;
      if (wake) { wake(); wake = null; }
    }
  })();

  const spoken = [];
  let first = true;
  while (!ttsInterrupted && isActive) {
    if (!queue.length) {
      if (producerDone) break;
      await new Promise(r => (wake = r));
      continue;
    }
    const item = queue.shift();
    let decoded;
    try {
      decoded = await item.audio;
    } catch (e) {
      showError('TTS: ' + e.message);
      break;
    }
    if (ttsInterrupted || !isActive) break;
    if (first) { T('primo-suono'); setState('speaking', 'hermes'); first = false; }
    await playBuffer(decoded);
    spoken.push(item.text);
  }

  await producer.catch(() => {});
  if (failed) showError('Chat: ' + failed);
  return spoken.join(' ');
}
