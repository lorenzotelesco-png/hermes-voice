import { useEffect, useRef, useState } from 'preact/hooks';
import { voice, DEBUG, type Snapshot } from '../voice/engine';
import { chat, type Item } from './store';
import { toolLabel } from './labels';

// Four blobs, one per voice band (low to high). While you talk they follow
// the mic, while Hermes talks they follow its voice, band by band, so each
// syllable moves them a little differently. While it thinks nothing is listened to:
// a slow wave runs across them instead, so thinking never looks like talking.
// Driven straight from requestAnimationFrame: 60 re-renders a second through
// state would be waste.
const PHASES = [0, 0.7, 1.4, 2.1];

// Each band is measured against its own recent peak. A voice lives mostly in
// the low bands; without this the right-hand blobs would barely move. The
// peak never falls below MIN_PEAK, so near-silence stays still.
const MIN_PEAK = 0.18;
const PEAK_DECAY = 0.996;   // per frame: a loud word stops dominating in a few seconds
const GATE = 0.02;

class Levels {
  raw = [0, 0, 0, 0];
  level = [0, 0, 0, 0];
  peak = [MIN_PEAK, MIN_PEAK, MIN_PEAK, MIN_PEAK];

  update() {
    voice.levels(this.raw);
    let sum = 0;
    for (let b = 0; b < 4; b++) {
      const v = this.raw[b] < GATE ? 0 : this.raw[b];
      this.peak[b] = Math.max(v, this.peak[b] * PEAK_DECAY, MIN_PEAK);
      const target = Math.min(1, v / this.peak[b]);
      // Rises a little faster than it falls, so words land and then settle.
      this.level[b] += (target - this.level[b]) * (target > this.level[b] ? 0.22 : 0.09);
      sum += this.level[b];
    }
    return sum / 4;
  }
}

// What is drawn is never the target itself but a spring chasing it: frame to
// frame jitter in the sound and the jump from one state to the next both come
// out as one continuous, slightly elastic movement.
const OMEGA = 16;      // rad/s: how quickly it follows
const ZETA = 0.72;     // below 1 overshoots a touch, which reads as organic

class Spring {
  x: number;
  v = 0;
  constructor(x: number) { this.x = x; }
  step(target: number, dt: number) {
    this.v += (OMEGA * OMEGA * (target - this.x) - 2 * ZETA * OMEGA * this.v) * dt;
    this.x += this.v * dt;
    return this.x;
  }
}

class Blob {
  sx = new Spring(1);
  sy = new Spring(1);
  ty = new Spring(0);
  op = new Spring(0.6);
}

function targets(state: string, muted: boolean, e: number, i: number, t: number) {
  const p = PHASES[i];
  if (state === 'speaking') {
    // Taller as the band sounds, never more than about half again: the
    // shape stays a blob, not a lozenge.
    const sy = 0.96 + e * 0.58 + 0.03 * Math.sin(t * 2.2 + p);
    return { sx: 1 / (0.92 + sy * 0.08), sy, ty: 0, op: 0.78 + e * 0.22 };
  }
  if (state === 'listening' && !muted) {
    const sy = 1 + 0.045 * Math.sin(t * 1.6 + p) + e * 0.5;
    return { sx: 1 / (0.95 + sy * 0.05), sy, ty: 0, op: 0.6 + Math.min(0.4, 0.06 + e * 0.8) };
  }
  if (state === 'thinking') {
    // Round, a little smaller, a wave of light travelling left to right.
    const k = (((t * 0.5 - i * 0.2) % 1) + 1) % 1;
    const pulse = Math.pow(Math.sin(k * Math.PI), 2);
    const s = 0.8 + 0.1 * pulse;
    return { sx: s, sy: s * 0.86, ty: -7 * pulse, op: 0.35 + 0.6 * pulse };
  }
  // Calibrating, or muted: barely breathing.
  const b = Math.sin(t * 1.1 + p);
  return { sx: 1 - 0.02 * b, sy: 1 + 0.04 * b, ty: 0, op: muted ? 0.3 : 0.55 + 0.1 * b };
}

function draw(stage: HTMLElement, els: HTMLDivElement[], lv: Levels, blobs: Blob[], t: number, dt: number) {
  const { state, muted } = voice.snapshot;
  const energy = lv.update();
  stage.style.setProperty('--level', energy.toFixed(3));
  els.forEach((el, i) => {
    if (!el) return;
    const g = targets(state, muted, lv.level[i], i, t);
    const b = blobs[i];
    const sx = b.sx.step(g.sx, dt);
    const sy = b.sy.step(g.sy, dt);
    const ty = b.ty.step(g.ty, dt);
    const op = Math.min(1, Math.max(0, b.op.step(g.op, dt)));
    el.style.transform = `translateY(${ty.toFixed(2)}px) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
    el.style.opacity = op.toFixed(3);
  });
}

const LABELS: Record<string, string> = {
  calibrating: 'preparo il microfono',
  listening: 'ti ascolto',
  thinking: 'ci penso',
  speaking: 'hermes',
};

function runningTool(items: Item[]) {
  for (let k = items.length - 1; k >= 0; k--) {
    const it = items[k];
    if (it.role === 'tool') return it.state === 'running' ? it : null;
    if (it.role === 'user') return null;
  }
  return null;
}

export function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  );
}

export function WaveIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <line x1="5" y1="10" x2="5" y2="14" />
      <line x1="9.5" y1="6" x2="9.5" y2="18" />
      <line x1="14.5" y1="8.5" x2="14.5" y2="15.5" />
      <line x1="19" y1="10.5" x2="19" y2="13.5" />
    </svg>
  );
}

/** Voice mode: full screen, above the tabs. Closing it ends the voice session and leaves the chat, with the transcript. */
export function VoiceStage() {
  const [snap, setSnap] = useState<Snapshot>(voice.snapshot);
  const [tool, setTool] = useState<Extract<Item, { role: 'tool' }> | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const blobRefs = useRef<HTMLDivElement[]>([]);

  useEffect(() => voice.subscribe(setSnap), []);
  useEffect(() => chat.subscribe(s => setTool(runningTool(s.items))), []);

  useEffect(() => {
    const levels = new Levels();
    const blobs = PHASES.map(() => new Blob());
    let raf = 0;
    let last = 0;
    const loop = (ts: number) => {
      // Real elapsed time, capped: after a stall (app in the background) the
      // springs resume from where they were instead of leaping.
      const dt = last ? Math.min((ts - last) / 1000, 1 / 30) : 1 / 60;
      last = ts;
      if (stageRef.current) draw(stageRef.current, blobRefs.current, levels, blobs, ts / 1000, dt);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const sub = snap.warn ? snap.warn
    : snap.muted ? 'microfono spento'
    : snap.state === 'speaking' ? 'tocca per interrompere'
    : snap.state === 'thinking' && tool ? [toolLabel(tool.name), tool.preview].filter(Boolean).join(' · ')
    : '';

  return (
    // A tap anywhere wakes the audio if iOS suspended it, and while Hermes
    // speaks cuts the reply short.
    <div ref={stageRef} class={`voice-stage is-${snap.state}${snap.muted ? ' is-muted' : ''}`}
         role="dialog" aria-label="Conversazione a voce"
         onClick={() => {
           voice.wake();
           if (voice.snapshot.state === 'speaking') voice.interrupt();
         }}>
      {DEBUG && snap.debug && <div class="debug">{snap.debug}</div>}
      <div class="blobs">
        {PHASES.map((_, i) => (
          <div class="blob" key={i} ref={el => { if (el) blobRefs.current[i] = el; }} />
        ))}
      </div>
      <div class="voice-label">{LABELS[snap.state] || snap.label}</div>
      <div class={`voice-sub${snap.warn ? ' is-warn' : ''}`}>{sub}</div>
      <div class="voice-controls" onClick={e => e.stopPropagation()}>
        <button class={`ctrl ctrl-mute${snap.muted ? ' is-muted' : ''}`}
                title={snap.muted ? 'Riattiva il microfono' : 'Spegni il microfono'}
                aria-pressed={snap.muted} onClick={() => voice.toggleMute()}>
          <MicIcon />
        </button>
        <button class="ctrl ctrl-close" title="Chiudi la voce" onClick={() => voice.stop()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
    </div>
  );
}
