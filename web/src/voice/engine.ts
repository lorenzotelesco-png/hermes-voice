// The voice pipeline: VAD, recording, STT, streamed speech, barge-in. Ported
// from the old app.js; it reports state to whoever subscribes instead of
// touching the DOM, so it can live on while the user is on another tab.
//
// The conversation itself belongs to the chat store: a spoken turn is a
// message in the same Hermes session as the typed ones, and its transcript
// appears in the thread like any other.

import { chat } from '../chat/store';

export type VoiceState = 'off' | 'calibrating' | 'listening' | 'thinking' | 'speaking';

export interface Snapshot {
  state: VoiceState;
  label: string;
  muted: boolean;
  debug: string;
  warn: string;      // something the user has to do, e.g. tap to wake the audio
}

type Listener = (s: Snapshot) => void;

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

// Lowest noise floor the thresholds are computed from. Measured on the phone
// (float samples) a quiet room is ~0.03 and a spoken turn peaks ~11: with the
// old floor of 3 the bar to open a turn sat at 8.4, too close to the voice.
// Barge-in keeps the old floor, because what it must not mistake for speech
// is our own reply leaking past echo cancellation, not the room.
const MIN_FLOOR = qs('floor', 2);
const MIN_BARGE_FLOOR = 3;

// Four bands across the voice range, one per blob on screen, low to high.
// Speech moves between them syllable by syllable, which is what makes the
// animation follow the words instead of just their loudness.
const BANDS: [number, number][] = [[90, 300], [300, 900], [900, 2200], [2200, 6000]];

// Past this the provider is not coming back: the relay gets the turn instead.
const STT_DIRECT_TIMEOUT_MS = 15000;

const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

// What the phone's audio stack does is invisible from the server, and "it
// just keeps listening" has half a dozen possible causes. A few facts per
// session go to the hub's journal instead: context state, the noise floor,
// the loudest sound heard, what the transcription returned.
function diag(event: string, data: Record<string, unknown> = {}) {
  try {
    fetch('/api/diag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, data }),
      keepalive: true,
    }).catch(() => {});
  } catch { /* diagnostics must never break the voice */ }
}

interface SttDirect {
  mode: string; wire: string; provider: string; model: string;
  base_url: string; api_key: string; language?: string;
}

export function getAudioMime() {
  const types = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', ''];
  return types.find(t => !t || MediaRecorder.isTypeSupported(t)) || '';
}

export function mimeToExt(mime: string) {
  if (!mime) return '.webm';
  if (mime.includes('mp4')) return '.m4a';
  if (mime.includes('ogg')) return '.ogg';
  if (mime.includes('mpeg')) return '.mp3';
  return '.webm';
}

export class VoiceEngine {
  /** Smoothed input level, read by the animation every frame. */
  volume = 0;
  onError: (msg: string) => void = () => {};

  private snap: Snapshot = { state: 'off', label: '', muted: false, debug: '', warn: '' };
  private listeners = new Set<Listener>();

  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;       // the mic
  private outAnalyser: AnalyserNode | null = null;    // Hermes' voice, on its way to the speaker
  private freq: Uint8Array<ArrayBuffer> | null = null;
  private micFloor = [0, 0, 0, 0];                    // the room's own noise, per band
  private calibBands: number[][] = [];
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
  private calibStarted = false;
  private calibSamples: number[] = [];
  private loudest = 0;            // since calibration, for the diagnostics
  private noSignal = false;
  private speechStarts = 0;

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
    // The screen opens on the tap, not after the permission prompt: waiting
    // for the microphone first looked like a button that did nothing.
    this.calibrating = true;
    this.calibStarted = false;
    this.set({ muted: false, debug: '', warn: '' });
    this.setState('calibrating', 'calibrazione...');

    // iOS lets audio start only inside the tap that asked for it. The context
    // used to be created after awaiting the microphone, and a permission
    // prompt outlasts that window: the context stayed suspended, the analyser
    // read pure silence, and the VAD never heard a word. So: create and resume
    // it now, before anything is awaited.
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    const ctx: AudioContext = new Ctx();
    const resumed = ctx.resume().catch(() => {});
    this.audioCtx = ctx;
    // Calls, Siri and other apps suspend it later on; a tap brings it back.
    ctx.onstatechange = () => this.checkAudio();

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
      diag('mic-denied', { error: e.name, message: e.message });
      ctx.close().catch(() => {});
      this.audioCtx = null;
      this.setState('off', '');
      this.onError('Microfono non disponibile: ' + e.message);
      return;
    }
    if (!this.active || this.audioCtx !== ctx) {   // closed while the prompt was up
      this.stream.getTracks().forEach(t => t.stop());
      return;
    }
    await Promise.race([resumed, sleep(1000)]);

    // No custom sampleRate — let Safari use its native rate
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    // Only the frequency data (the animation) is smoothed; the VAD reads the
    // time-domain samples, which this does not touch.
    this.analyser.smoothingTimeConstant = 0.75;
    ctx.createMediaStreamSource(this.stream).connect(this.analyser);
    // Everything Hermes says passes through here on its way out, so the
    // animation can move with the actual sound of the reply.
    this.outAnalyser = ctx.createAnalyser();
    this.outAnalyser.fftSize = 1024;
    this.outAnalyser.smoothingTimeConstant = 0.75;
    this.outAnalyser.minDecibels = -85;
    this.outAnalyser.maxDecibels = -20;
    this.outAnalyser.connect(ctx.destination);

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

    const track = this.stream.getAudioTracks()[0];
    diag('voice-start', {
      ctx: ctx.state, rate: ctx.sampleRate, mime: getAudioMime(),
      track: track ? { enabled: track.enabled, muted: track.muted, state: track.readyState } : null,
    });
    this.checkAudio();
  }

  /** A tap on the voice screen: the one thing iOS accepts to (re)start audio. */
  wake() {
    const ctx = this.audioCtx;
    if (!ctx || ctx.state === 'running') return;
    ctx.resume().then(() => this.checkAudio()).catch(() => {});
  }

  private checkAudio() {
    const ctx = this.audioCtx;
    if (!ctx || !this.active || !this.analyser) return;
    if (ctx.state === 'running') {
      if (this.snap.warn) this.set({ warn: '' });
      if (!this.calibStarted) this.calibrate();
    } else {
      diag('audio-suspended', { ctx: ctx.state, voice: this.snap.state });
      this.set({ warn: 'Tocca lo schermo per attivare il microfono' });
    }
  }

  // Calibrate the noise floor for 1.5 seconds, on a running context only:
  // measured on a suspended one it would be silence, and so would everything after.
  private calibrate() {
    this.noSignal = false;
    this.calibStarted = true;
    this.calibSamples = [];
    this.calibBands = [];
    setTimeout(() => {
      if (!this.active) return;
      const s = this.calibSamples;
      this.noiseFloor = Math.max(MIN_FLOOR, s.length ? s.reduce((a, b) => a + b, 0) / s.length : 5);
      // A little above the room's average, so its hum reads as stillness.
      const n = this.calibBands.length || 1;
      this.micFloor = this.micFloor.map((_, b) =>
        Math.min(0.9, 1.15 * this.calibBands.reduce((a, x) => a + x[b], 0) / n + 0.03));
      const peak = Math.max(0, ...s);
      diag('calibrated', {
        floor: +this.noiseFloor.toFixed(2), peak: +peak.toFixed(2), samples: s.length,
        ctx: this.audioCtx?.state, startAt: +(this.noiseFloor * START_MULT).toFixed(2),
      });
      if (peak === 0) {
        // Not one sample above absolute zero: no quiet room is that quiet.
        this.noSignal = true;
        this.set({ warn: 'Il microfono non dà segnale: tocca lo schermo' });
      }
      this.calibrating = false;
      this.loudest = 0;
      this.speechStarts = 0;
      this.setState('listening', 'in ascolto');
      this.monitorLoop();
      // Whether the VAD could have heard anything: the loudest sound against
      // the bar a turn has to clear. Twice, then quiet.
      for (const after of [10000, 30000]) {
        setTimeout(() => {
          if (!this.active) return;
          diag('listening', {
            after: after / 1000, loudest: +this.loudest.toFixed(2),
            startAt: +(this.noiseFloor * START_MULT).toFixed(2), turns: this.speechStarts,
            ctx: this.audioCtx?.state, muted: this.snap.muted,
          });
        }, after);
      }
    }, 1500);
    this.monitorCalib();
  }

  stop() {
    if (!this.active) return;
    this.setState('off', '');
    this.set({ warn: '' });
    clearTimeout(this.silenceTimer);
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    this.stream?.getTracks().forEach(t => t.stop());
    this.audioCtx?.close().catch(() => {});
    this.audioCtx = null;
    this.analyser = null;
    this.outAnalyser = null;
    if (this.currentAudio) { try { this.currentAudio.stop(); } catch { /* already ended */ } }
    this.currentAudio = null;
    this.isSpeaking = false;
    this.isProcessing = false;
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
    // Still writing? Then it is writing for nobody. A turn waiting on an
    // approval is left alone: that question is answered on the screen.
    if (chat.snapshot.running && !chat.snapshot.approval) chat.stop();
  }

  // ── VAD ─────────────────────────────────────────────────────────
  // Float samples, not bytes: at 8 bits anything under -42 dBFS reads as
  // exactly zero, which made a quiet room indistinguishable from a dead mic.
  private getRMS() {
    const d = new Float32Array(this.analyser!.fftSize);
    this.analyser!.getFloatTimeDomainData(d);
    let sum = 0;
    for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
    return Math.sqrt(sum / d.length) * 100;
  }

  private readBands(an: AnalyserNode, out: number[]) {
    const n = an.frequencyBinCount;
    if (!this.freq || this.freq.length !== n) this.freq = new Uint8Array(n);
    an.getByteFrequencyData(this.freq);
    const hz = an.context.sampleRate / an.fftSize;
    BANDS.forEach(([lo, hi], b) => {
      const i0 = Math.max(1, Math.floor(lo / hz));
      const i1 = Math.min(n - 1, Math.ceil(hi / hz));
      let sum = 0;
      for (let i = i0; i <= i1; i++) sum += this.freq![i];
      out[b] = sum / ((i1 - i0 + 1) * 255);
    });
    return out;
  }

  /**
   * How much each voice band is sounding right now, 0..1, low to high: the
   * mic while listening (less the room's noise), Hermes' voice while it
   * speaks, and nothing at all while it thinks.
   */
  levels(out: number[]) {
    const state = this.snap.state;
    if (state === 'speaking' && this.outAnalyser) return this.readBands(this.outAnalyser, out);
    if (state === 'listening' && this.analyser && !this.snap.muted) {
      this.readBands(this.analyser, out);
      for (let b = 0; b < out.length; b++) {
        out[b] = Math.max(0, (out[b] - this.micFloor[b]) / (1 - this.micFloor[b]));
      }
      return out;
    }
    out.fill(0);
    return out;
  }

  private monitorCalib() {
    if (!this.calibrating || !this.active) return;
    this.calibSamples.push(this.getRMS());
    this.calibBands.push(this.readBands(this.analyser!, [0, 0, 0, 0]));
    setTimeout(() => this.monitorCalib(), 80);
  }

  private monitorLoop() {
    if (!this.active || this.calibrating) return;
    const rms = this.getRMS();
    this.volume = this.volume * 0.75 + (rms / (this.noiseFloor * 8)) * 0.25;
    if (rms > this.loudest) this.loudest = rms;
    if (this.noSignal && rms > 0) {
      this.noSignal = false;
      diag('signal-back', { ctx: this.audioCtx?.state });
      this.set({ warn: '' });
    }

    const speaking = this.snap.state === 'speaking';
    const gateOpen = speaking
      ? (performance.now() - this.playbackStartedAt) > BARGE_IN_GRACE_MS
      : !this.isProcessing;

    // Already recording? Then the only job is to notice we are still talking,
    // and a much lower bar is enough for that.
    const threshold = this.isSpeaking ? this.noiseFloor * CONTINUE_MULT
                    : speaking ? Math.max(this.noiseFloor, MIN_BARGE_FLOOR) * BARGE_IN_MULT
                    : this.noiseFloor * START_MULT;

    if (!this.snap.muted && gateOpen && rms > threshold) {
      if (speaking) this.interrupt();
      if (!this.isSpeaking) this.startRecording();
      clearTimeout(this.silenceTimer);
      this.silenceTimer = window.setTimeout(() => this.endSpeech(), SILENCE_MS);
    }
    setTimeout(() => this.monitorLoop(), 40);
  }

  private startRecording() {
    this.speechStarts++;
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
    diag('speech', { ms: Math.round(performance.now() - this.speechStartedAt) });
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
      // From China the provider sometimes just hangs; the relay goes through the VPS.
      signal: AbortSignal.timeout(STT_DIRECT_TIMEOUT_MS),
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
      const t0 = performance.now();
      let text: string;
      let via = 'relay';
      if (this.sttDirect) {
        try {
          text = await this.transcribeDirect(blob, ext);
          via = 'direct';
          this.T('stt-diretto');
        } catch (e: any) {
          // A revoked key or a provider hiccup must not end the conversation:
          // drop to the relay for this turn and stop trying direct afterwards.
          console.warn('STT diretto fallito, passo al relay:', e.message);
          diag('stt-direct-failed', { error: e.name, message: e.message });
          this.sttDirect = null;
          text = await this.transcribeRelay(blob, ext);
          this.T('stt-relay');
        }
      } else {
        text = await this.transcribeRelay(blob, ext);
        this.T('stt');
      }
      diag('stt', { via, ms: Math.round(performance.now() - t0), bytes: blob.size, chars: text.length });
      if (text.length < 2) return;   // silent or noise
      if (chat.snapshot.approval) {
        this.onError('Prima rispondi alla richiesta sullo schermo');
        return;
      }

      this.ttsInterrupted = false;
      await this.speakReply(onSentence => chat.send(text, { voice: true, onSentence }));
    } catch (e: any) {
      diag('turn-error', { error: e.name, message: e.message });
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
      src.connect(this.outAnalyser || this.audioCtx!.destination);
      this.currentAudio = src;
      this.playbackStartedAt = performance.now();
      src.onended = () => {
        this.currentAudio = null;
        res();
      };
      src.start(0);
    });
  }

  // Speak the reply as it is written. The hub cuts sentences and pushes them
  // down the stream; each one starts synthesizing the moment it arrives, so
  // the first word is spoken while the model is still writing the rest.
  private async speakReply(run: (onSentence: (text: string) => void) => Promise<void>) {
    const queue: { text: string; audio: Promise<AudioBuffer> }[] = [];
    // Cast, not annotation: otherwise TS narrows it to null for good and
    // cannot see the assignment made inside the Promise executor below.
    let wake = null as (() => void) | null;
    let producerDone = false;
    let nSent = 0;

    run(text => {
      this.T('frase' + (++nSent));
      // Synthesis starts here, not at playback time: sentence N+1 is being
      // fetched while N is still playing.
      const audio = this.fetchAndDecodeTTS(text);
      audio.catch(() => {});   // handled when its turn to play comes
      queue.push({ text, audio });
      if (wake) { wake(); wake = null; }
    }).catch(() => {}).finally(() => {
      producerDone = true;
      if (wake) { wake(); wake = null; }
    });

    let first = true;
    while (!this.ttsInterrupted && this.active) {
      if (!queue.length) {
        if (producerDone) break;
        // Between sentences Hermes may be running a tool or waiting on an
        // approval for a long while: that is thinking, not speaking, and
        // keeps barge-in from cutting into a turn that is still working.
        if (!first) this.setState('thinking', 'lavoro...');
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
      if (first) { this.T('primo-suono'); first = false; }
      this.setState('speaking', 'hermes');
      await this.playBuffer(decoded);
    }
  }
}

export const voice = new VoiceEngine();
