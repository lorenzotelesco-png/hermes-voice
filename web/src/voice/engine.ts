// The voice pipeline: VAD, recording, STT, streamed reply, streamed speech,
// barge-in. Ported from the old app.js with its behaviour unchanged; the only
// difference is that it reports state to whoever subscribes instead of
// touching the DOM, so it can live on while the user is on another tab.

export type VoiceState = 'off' | 'calibrating' | 'listening' | 'thinking' | 'speaking';

export interface Snapshot {
  state: VoiceState;
  label: string;
  muted: boolean;
  debug: string;
}

type Listener = (s: Snapshot) => void;

const NL = String.fromCharCode(10);
const SSE_SEP = NL + NL;

const params = new URLSearchParams(location.search);
const qs = (k: string, d: number) => Number(params.get(k)) || d;

// Where the time actually goes, shown on the phone (?debug=1, or the switch in
// "Altro"). Guessing by ear cannot tell "the model is slow" from "the sentences
// all arrive at once because something buffered the stream" — and those need
// opposite fixes.
export const DEBUG_KEY = 'hub.debug';
export const DEBUG = params.has('debug') || readFlag(DEBUG_KEY);

function readFlag(key: string) {
  try { return localStorage.getItem(key) === '1'; } catch { return false; }
}

// Endpointing: how long a pause must last before the turn is considered over.
// Dead time paid on every turn, so it is tempting to shrink — but cutting the
// speaker off mid-sentence costs a whole retry, which is far worse than 300ms.
const SILENCE_MS = qs('silence', 900);

// Two thresholds, not one. Entering speech has to clear a high bar so room
// noise cannot open a turn; STAYING in speech only has to clear a low one,
// because natural speech constantly dips — between words, on unvoiced
// consonants, in mid-sentence pauses for thought. With a single bar every one
// of those dips looks like the end of the turn.
const START_MULT = qs('start', 2.8);
const CONTINUE_MULT = qs('cont', 1.35);

// A burst shorter than this is a cough, a door, a keyboard — not a turn.
const MIN_UTTERANCE_MS = qs('minms', 500);

// Barge-in: the mic stays live while Hermes speaks so it can be interrupted.
// The bar is higher than for normal listening because the mic still picks up
// some of our own output even with AEC on, and a short grace period after audio
// starts stops the first syllable of the reply from tripping the detector.
const BARGE_IN_MULT = 4.0;
const BARGE_IN_GRACE_MS = 500;

interface SttDirect {
  mode: string; wire: string; provider: string; model: string;
  base_url: string; api_key: string; language?: string;
}

function makeSessionId() {
  return crypto.randomUUID();
}

function getAudioMime() {
  const types = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', ''];
  return types.find(t => !t || MediaRecorder.isTypeSupported(t)) || '';
}

function mimeToExt(mime: string) {
  if (!mime) return '.webm';
  if (mime.includes('mp4')) return '.m4a';
  if (mime.includes('ogg')) return '.ogg';
  if (mime.includes('mpeg')) return '.mp3';
  return '.webm';
}

// Parse an SSE body incrementally. EventSource cannot be used because the chat
// call is a POST, so the framing is handled here: events are separated by a
// blank line, and only "data:" lines carry payload.
async function* sseEvents(res: Response): AsyncGenerator<any> {
  const reader = res.body!.getReader();
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
        try { yield JSON.parse(line.slice(5).trim()); } catch { /* comment or partial */ }
      }
    }
  }
}

export class VoiceEngine {
  /** Smoothed input level, read by the animation every frame. */
  volume = 0;
  onError: (msg: string) => void = () => {};

  private snap: Snapshot = { state: 'off', label: '', muted: false, debug: '' };
  private listeners = new Set<Listener>();

  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private currentAudio: AudioBufferSourceNode | null = null;

  private isSpeaking = false;     // the user, that is: a recording is open
  private isProcessing = false;
  private silenceTimer: number | undefined;
  private speechStartedAt = 0;
  private playbackStartedAt = 0;
  private ttsInterrupted = false;

  private noiseFloor = 5;
  private calibrating = true;
  private calibSamples: number[] = [];

  private history: { role: string; content: string }[] = [];
  private sessionId: string | null = null;
  // STT settings fetched once per session. Held in memory only — this carries
  // a provider credential and must never reach localStorage or a URL.
  private sttDirect: SttDirect | null = null;

  private mark0 = 0;
  private marks: [string, number][] = [];

  // ── state for subscribers ───────────────────────────────────────
  get snapshot() { return this.snap; }
  get active() { return this.snap.state !== 'off'; }

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    fn(this.snap);
    return () => { this.listeners.delete(fn); };
  }

  private set(patch: Partial<Snapshot>) {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach(fn => fn(this.snap));
  }

  private setState(state: VoiceState, label: string) {
    this.set({ state, label });
  }

  private T(label: string) {
    if (!DEBUG) return;
    this.marks.push([label, performance.now() - this.mark0]);
    this.set({ debug: this.marks.map(([l, ms]) => `${l} ${(ms / 1000).toFixed(2)}`).join('  ') });
  }

  // ── session ─────────────────────────────────────────────────────
  async start() {
    if (this.active) return;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Hardware echo cancellation is what makes barge-in possible at all
          // on a phone speaker: without it the mic hears our own reply and
          // every sentence interrupts itself.
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
    } catch (e: any) {
      this.onError('Microfono non disponibile: ' + e.message);
      return;
    }
    this.sessionId = makeSessionId();

    // No custom sampleRate — let Safari use its native rate
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    this.audioCtx = new Ctx();
    if (this.audioCtx.state === 'suspended') await this.audioCtx.resume();
    this.analyser = this.audioCtx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.5;
    this.audioCtx.createMediaStreamSource(this.stream).connect(this.analyser);

    // Fire and forget: if it is slow or fails we simply use the relay.
    fetch('/api/voice-config')
      .then(r => r.json())
      .then(cfg => {
        const stt = cfg && cfg.stt;
        if (stt && stt.mode === 'direct' && stt.wire === 'openai-multipart'
            && stt.base_url && stt.api_key) {
          this.sttDirect = stt;
          console.log('STT diretto:', stt.provider, stt.model);
        } else {
          console.log('STT via relay:', (stt && stt.reason) || 'non disponibile');
        }
      })
      .catch(() => {});

    this.calibrating = true;
    this.calibSamples = [];
    this.set({ muted: false, debug: '' });
    this.setState('calibrating', 'calibrazione...');

    // Calibrate the noise floor for 1.5 seconds
    setTimeout(() => {
      if (!this.active) return;
      const s = this.calibSamples;
      this.noiseFloor = Math.max(3, s.length ? s.reduce((a, b) => a + b, 0) / s.length : 5);
      this.calibrating = false;
      this.setState('listening', 'in ascolto');
      this.monitorLoop();
    }, 1500);
    this.monitorCalib();
  }

  stop() {
    if (!this.active) return;
    this.setState('off', '');
    clearTimeout(this.silenceTimer);
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.stream?.getTracks().forEach(t => t.stop());
    this.audioCtx?.close();
    if (this.currentAudio) { try { this.currentAudio.stop(); } catch { /* already ended */ } }
    this.currentAudio = null;
    this.isSpeaking = false;
    this.isProcessing = false;
    this.history = [];
    this.sessionId = null;
    this.sttDirect = null;
    this.volume = 0;
    this.set({ muted: false });
  }

  toggleMute() {
    if (!this.stream) return;
    const muted = !this.snap.muted;
    // Muting mid-utterance means "I am done, take it now". No VAD heuristic is
    // right every time, so there has to be a way to end a turn on purpose —
    // especially for a long pause the detector would otherwise cut into.
    if (muted && this.isSpeaking) {
      clearTimeout(this.silenceTimer);
      this.endSpeech();
    }
    this.stream.getAudioTracks().forEach(t => (t.enabled = !muted));
    this.set({ muted });
  }

  /** Cut the reply short (a tap while Hermes is speaking, or a barge-in). */
  interrupt() {
    this.ttsInterrupted = true;
    // The turn is abandoned, so drop the processing flag right away —
    // otherwise the recorder about to start would be gated out by it.
    this.isProcessing = false;
    if (this.currentAudio) { try { this.currentAudio.stop(); } catch { /* already ended */ } }
  }

  // ── VAD ─────────────────────────────────────────────────────────
  private getRMS() {
    const d = new Uint8Array(this.analyser!.frequencyBinCount);
    this.analyser!.getByteTimeDomainData(d);
    let sum = 0;
    for (let i = 0; i < d.length; i++) { const v = (d[i] - 128) / 128; sum += v * v; }
    return Math.sqrt(sum / d.length) * 100;
  }

  private monitorCalib() {
    if (!this.calibrating || !this.active) return;
    this.calibSamples.push(this.getRMS());
    setTimeout(() => this.monitorCalib(), 80);
  }

  private monitorLoop() {
    if (!this.active || this.calibrating) return;
    const rms = this.getRMS();
    this.volume = this.volume * 0.75 + (rms / (this.noiseFloor * 8)) * 0.25;

    const speaking = this.snap.state === 'speaking';
    const gateOpen = speaking
      ? (performance.now() - this.playbackStartedAt) > BARGE_IN_GRACE_MS
      : !this.isProcessing;

    // Already recording? Then the only job is to notice we are still talking,
    // and a much lower bar is enough for that.
    const threshold = this.noiseFloor * (this.isSpeaking ? CONTINUE_MULT
                                       : speaking ? BARGE_IN_MULT
                                       : START_MULT);

    if (!this.snap.muted && gateOpen && rms > threshold) {
      if (speaking) this.interrupt();
      if (!this.isSpeaking) this.startRecording();
      clearTimeout(this.silenceTimer);
      this.silenceTimer = window.setTimeout(() => this.endSpeech(), SILENCE_MS);
    }
    setTimeout(() => this.monitorLoop(), 40);
  }

  private startRecording() {
    this.isSpeaking = true;
    this.chunks = [];
    this.speechStartedAt = performance.now();
    this.recorder = new MediaRecorder(this.stream!, { mimeType: getAudioMime() });
    this.recorder.ondataavailable = e => { if (e.data.size > 0) this.chunks.push(e.data); };
    this.recorder.start(100);
  }

  private endSpeech() {
    if (!this.isSpeaking || this.isProcessing) return;
    const recorder = this.recorder!;

    // Too short to be a turn: throw it away and keep listening rather than
    // paying a transcription round trip for a cough.
    if (performance.now() - this.speechStartedAt < MIN_UTTERANCE_MS) {
      this.isSpeaking = false;
      if (recorder.state !== 'inactive') {
        recorder.onstop = () => { this.chunks = []; };
        recorder.stop();
      }
      if (this.active) this.setState('listening', 'in ascolto');
      return;
    }

    this.isSpeaking = false;
    this.isProcessing = true;
    this.mark0 = performance.now();
    this.marks = [];
    this.T('fine-voce');
    this.setState('thinking', 'elaboro...');
    recorder.onstop = async () => {
      const blob = new Blob(this.chunks, { type: recorder.mimeType || 'audio/webm' });
      await this.handleAudio(blob, recorder.mimeType);
      this.isProcessing = false;
      // A barge-in may already have started the next recording while this
      // turn was unwinding; do not reset the UI out from under it.
      if (this.active && !this.isSpeaking) { this.volume = 0; this.setState('listening', 'in ascolto'); }
    };
    if (recorder.state !== 'inactive') recorder.stop();
  }

  // ── pipeline ────────────────────────────────────────────────────
  // Straight to the provider, skipping tunnel, server and dashboard. Measured
  // from a phone those hops cost about as much as the transcription itself.
  private async transcribeDirect(blob: Blob, ext: string) {
    const stt = this.sttDirect!;
    const fd = new FormData();
    fd.append('file', blob, 'speech' + ext);
    fd.append('model', stt.model);
    if (stt.language) fd.append('language', stt.language);
    const res = await fetch(stt.base_url + '/audio/transcriptions', {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + stt.api_key },
      body: fd,
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json();
    return (d.text || '').trim() as string;
  }

  private async transcribeRelay(blob: Blob, ext: string) {
    const fd = new FormData();
    fd.append('audio', blob, 'speech' + ext);
    const res = await fetch('/api/transcribe', { method: 'POST', body: fd });
    const d = await res.json();
    if (d.error) throw new Error(d.error);
    return (d.text || '').trim() as string;
  }

  private async handleAudio(blob: Blob, mime: string) {
    try {
      const ext = mimeToExt(mime);
      let text: string;
      if (this.sttDirect) {
        try {
          text = await this.transcribeDirect(blob, ext);
          this.T('stt-diretto');
        } catch (e: any) {
          // A revoked key or a provider hiccup must not end the conversation:
          // drop to the relay for this turn and stop trying direct afterwards.
          console.warn('STT diretto fallito, passo al relay:', e.message);
          this.sttDirect = null;
          text = await this.transcribeRelay(blob, ext);
          this.T('stt-relay');
        }
      } else {
        text = await this.transcribeRelay(blob, ext);
        this.T('stt');
      }
      if (text.length < 2) return;   // silent or noise

      this.history.push({ role: 'user', content: text });

      this.ttsInterrupted = false;
      const chatRes = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ history: this.history.slice(-8), session_id: this.sessionId }),
      });
      if (!chatRes.ok || !chatRes.body) {
        const err = await chatRes.json().catch(() => ({}));
        this.onError('Chat: ' + (err.error || chatRes.status));
        return;
      }

      const spoken = await this.speakStream(chatRes);
      if (!spoken) return;
      // Record what was actually said, not what was generated. If the user cut
      // in, the agent should see a truncated turn — otherwise it carries on as
      // though the whole reply had landed.
      this.history.push({
        role: 'assistant',
        content: this.ttsInterrupted ? spoken + ' [interrotto dall utente]' : spoken,
      });
    } catch (e: any) {
      this.onError('Errore: ' + e.message);
      console.error(e);
    }
  }

  private async fetchAndDecodeTTS(text: string) {
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const bytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0)).buffer;
    return this.audioCtx!.decodeAudioData(bytes);
  }

  private playBuffer(decoded: AudioBuffer) {
    return new Promise<void>(res => {
      const src = this.audioCtx!.createBufferSource();
      src.buffer = decoded;
      src.connect(this.audioCtx!.destination);
      this.currentAudio = src;
      this.playbackStartedAt = performance.now();
      src.onended = () => {
        this.currentAudio = null;
        res();
      };
      src.start(0);
    });
  }

  // Speak the reply as it is written. The server cuts sentences and pushes
  // them down the stream; each one starts synthesizing the moment it arrives,
  // so the first word is spoken while the model is still writing the rest.
  //
  // Returns the text actually spoken, which is not the full reply when the
  // user barged in — the agent is told what it managed to say.
  private async speakStream(res: Response) {
    const queue: { text: string; audio: Promise<AudioBuffer> }[] = [];
    // Cast, not annotation: otherwise TS narrows it to null for good and
    // cannot see the assignment made inside the Promise executor below.
    let wake = null as (() => void) | null;
    let producerDone = false;
    let failed: string | null = null;
    let nSent = 0;

    const producer = (async () => {
      try {
        for await (const ev of sseEvents(res)) {
          if (ev.error) { failed = ev.error; break; }
          if (ev.done) break;
          if (!ev.sentence) continue;
          this.T('frase' + (++nSent));
          // Synthesis starts here, not at playback time: sentence N+1 is being
          // fetched while N is still playing.
          const audio = this.fetchAndDecodeTTS(ev.sentence);
          audio.catch(() => {});   // handled when its turn to play comes
          queue.push({ text: ev.sentence, audio });
          if (wake) { wake(); wake = null; }
        }
      } catch (e: any) {
        failed = e.message;
      } finally {
        producerDone = true;
        if (wake) { wake(); wake = null; }
      }
    })();

    const spoken: string[] = [];
    let first = true;
    while (!this.ttsInterrupted && this.active) {
      if (!queue.length) {
        if (producerDone) break;
        await new Promise<void>(r => (wake = r));
        continue;
      }
      const item = queue.shift()!;
      let decoded: AudioBuffer;
      try {
        decoded = await item.audio;
      } catch (e: any) {
        this.onError('TTS: ' + e.message);
        break;
      }
      if (this.ttsInterrupted || !this.active) break;
      if (first) { this.T('primo-suono'); this.setState('speaking', 'hermes'); first = false; }
      await this.playBuffer(decoded);
      spoken.push(item.text);
    }

    await producer.catch(() => {});
    if (failed) this.onError('Chat: ' + failed);
    return spoken.join(' ');
  }
}

export const voice = new VoiceEngine();
