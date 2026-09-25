import { useEffect, useRef, useState } from 'preact/hooks';
import { voice, DEBUG, type Snapshot } from '../voice/engine';

// Four blobs that breathe while idle, follow the mic while listening, pulse in
// sequence while thinking and ripple while Hermes speaks. Driven straight from
// requestAnimationFrame: re-rendering at 60fps through state would be waste.
const PHASES = [0, 0.7, 1.4, 2.1];

function animate(blobs: HTMLDivElement[], t: number) {
  const state = voice.snapshot.state;
  const volume = voice.volume;
  blobs.forEach((b, i) => {
    let sy = 1, sx = 1, op = 1;
    const p = PHASES[i];
    if (state === 'listening') {
      const boost = volume * (1 + 0.4 * Math.sin(t * 8 + p));
      sy = 1 + 0.12 * Math.sin(t * 3.5 + p) + boost * 0.7;
      sx = 1 / (0.95 + sy * 0.05);
      op = 0.7 + 0.3 * Math.min(1, volume * 3 + 0.2);
    } else if (state === 'speaking') {
      const wave = Math.sin(t * 9 + p * 1.5);
      sy = 1.1 + 0.45 * wave + 0.1 * Math.sin(t * 5 + p);
      sx = 1 / (0.92 + sy * 0.08);
    } else if (state === 'thinking') {
      const k = (t * 0.6 + i * 0.18) % 1;
      op = 0.2 + 0.75 * Math.pow(Math.sin(k * Math.PI), 2);
      sy = 0.75 + 0.3 * Math.pow(Math.sin(k * Math.PI), 2);
    } else {
      sy = 1 + 0.06 * Math.sin(t * 1.2 + p);
      sx = 1 - 0.03 * Math.sin(t * 1.2 + p);
      op = 0.85 + 0.1 * Math.sin(t + p);
    }
    b.style.transform = `scaleY(${sy.toFixed(3)}) scaleX(${sx.toFixed(3)})`;
    b.style.opacity = op.toFixed(3);
  });
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

/** The voice session, docked under the thread so the transcript stays in view. */
export function VoiceDock() {
  const [snap, setSnap] = useState<Snapshot>(voice.snapshot);
  const blobRefs = useRef<HTMLDivElement[]>([]);

  useEffect(() => voice.subscribe(setSnap), []);

  useEffect(() => {
    let raf = 0;
    const loop = (ts: number) => {
      animate(blobRefs.current, ts / 1000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    // A tap anywhere on the dock while Hermes speaks cuts the reply short.
    <div class={`voice-dock is-${snap.state}`}
         onClick={() => { if (voice.snapshot.state === 'speaking') voice.interrupt(); }}>
      {DEBUG && snap.debug && <div class="debug">{snap.debug}</div>}
      <div class="dock-stage">
        <div class="blobs">
          {PHASES.map((_, i) => (
            <div class="blob" key={i} ref={el => { if (el) blobRefs.current[i] = el; }} />
          ))}
        </div>
        <div class="voice-label">{snap.state === 'speaking' ? 'tocca per interrompere' : snap.label}</div>
      </div>
      <div class="voice-controls" onClick={e => e.stopPropagation()}>
        <button class={`ctrl ctrl-mute${snap.muted ? ' is-muted' : ''}`} title="Muto"
                aria-pressed={snap.muted} onClick={() => voice.toggleMute()}>
          <MicIcon />
        </button>
        <button class="ctrl ctrl-end" title="Termina la voce" onClick={() => voice.stop()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /></svg>
        </button>
      </div>
    </div>
  );
}
