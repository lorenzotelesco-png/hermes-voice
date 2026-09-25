import { useEffect, useRef, useState } from 'preact/hooks';
import { voice, DEBUG, type Snapshot } from '../voice/engine';
import { chat, type Item } from './store';
import { toolLabel } from './labels';

// Four blobs, one per voice band (low to high). While you talk they follow
// the mic, while Hermes talks they follow its voice, band by band, so each
// syllable moves them differently. While it thinks nothing is listened to:
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

class Motion {
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
      // Syllables snap in and fade out: a fast rise, a slower fall.
      this.level[b] += (target - this.level[b]) * (target > this.level[b] ? 0.5 : 0.14);
      sum += this.level[b];
    }
    return sum / 4;
  }
}

function draw(stage: HTMLElement, blobs: HTMLDivElement[], m: Motion, t: number) {
  const { state, muted } = voice.snapshot;
  const energy = m.update();
  stage.style.setProperty('--level', energy.toFixed(3));
  blobs.forEach((el, i) => {
    if (!el) return;
    const e = m.level[i];
    const p = PHASES[i];
    let sx = 1, sy = 1, ty = 0, op = 1;
    if (state === 'speaking') {
      sy = 0.9 + e * 1.5;
      sx = 1 + e * 0.06;
      op = 0.72 + e * 0.28;
    } else if (state === 'listening' && !muted) {
      const idle = 0.035 * Math.sin(t * 1.6 + p);
      sy = 1 + idle + e * 1.25;
      sx = 1 - e * 0.1;
      op = 0.55 + Math.min(0.45, 0.08 + e);
    } else if (state === 'thinking') {
      // Round, small, a wave of light travelling left to right.
      const k = (((t * 0.55 - i * 0.2) % 1) + 1) % 1;
      const pulse = Math.pow(Math.sin(k * Math.PI), 2);
      sx = 0.74 + 0.12 * pulse;
      sy = sx * 0.84;
      ty = -9 * pulse;
      op = 0.28 + 0.66 * pulse;
    } else {
      // Calibrating, or muted: barely breathing.
      const b = Math.sin(t * 1.1 + p);
      sy = 1 + 0.04 * b;
      sx = 1 - 0.02 * b;
      op = muted ? 0.3 : 0.55 + 0.1 * b;
    }
    el.style.transform = `translateY(${ty.toFixed(1)}px) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
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
    const motion = new Motion();
    let raf = 0;
    const loop = (ts: number) => {
      if (stageRef.current) draw(stageRef.current, blobRefs.current, motion, ts / 1000);
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
