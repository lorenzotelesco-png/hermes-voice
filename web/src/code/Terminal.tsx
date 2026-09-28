import { useEffect, useRef, useState } from 'preact/hooks';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { Locked, code, codePost } from './lock';

const FONT_KEY = 'hub.term.font';
const ESC = '\x1b';

// What the iPhone keyboard lacks. Arrows follow the terminal's cursor mode:
// full-screen programs (OpenCode, vim, less) ask for the "application" codes.
const KEYS: { label: string; send: (app: boolean) => string }[] = [
  { label: 'esc', send: () => ESC },
  { label: 'tab', send: () => '\t' },
  { label: '↑', send: a => (a ? `${ESC}OA` : `${ESC}[A`) },
  { label: '↓', send: a => (a ? `${ESC}OB` : `${ESC}[B`) },
  { label: '←', send: a => (a ? `${ESC}OD` : `${ESC}[D`) },
  { label: '→', send: a => (a ? `${ESC}OC` : `${ESC}[C`) },
  { label: '|', send: () => '|' },
  { label: '~', send: () => '~' },
  { label: '/', send: () => '/' },
  { label: '-', send: () => '-' },
];

interface Info { id: string; title: string; dir: string; running: boolean }

export function Terminal({ id, onLocked }: { id: string; onLocked: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const page = useRef<HTMLElement>(null);
  const [info, setInfo] = useState<Info | null>(null);
  const [state, setState] = useState<'connecting' | 'open' | 'lost' | 'ended'>('connecting');
  const [ctrl, setCtrl] = useState(false);
  const ctrlRef = useRef(false);
  const api = useRef<{ send: (s: string) => void; term: XTerm | null; zoom: (d: number) => void }>({ send: () => {}, term: null, zoom: () => {} });

  useEffect(() => {
    code<{ terminals: Info[] }>('/api/code/pty')
      .then(d => setInfo(d.terminals.find(t => t.id === id) || null))
      .catch(e => { if (e instanceof Locked) onLocked(); });
  }, [id]);

  useEffect(() => {
    let font = 12;
    try { font = Number(localStorage.getItem(FONT_KEY)) || 12; } catch { /* default */ }
    const term = new XTerm({
      fontSize: font, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', cursorBlink: true,
      scrollback: 5000, allowProposedApi: false,
      theme: { background: '#000000', foreground: '#e8e6e3', cursor: '#c6d8f8', selectionBackground: '#3a4a66' },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current!);

    let ws: WebSocket | null = null;
    let cursor = 0;            // replay everything the first time: reopening shows the history
    let closedByUs = false;
    let retry: number | undefined;
    let lastSize = '';
    let sizeTimer: number | undefined;

    // A replay written while the terminal is being resized is kept but not
    // drawn until the next write; draw it now.
    let paintQueued = false;
    const repaint = () => {
      if (paintQueued) return;
      paintQueued = true;
      requestAnimationFrame(() => { paintQueued = false; term.refresh(0, term.rows - 1); });
    };

    const sendSize = () => {
      const size = `${term.rows}x${term.cols}`;
      if (size === lastSize) return;
      lastSize = size;
      codePost(`/api/code/pty/${id}/size`, { rows: term.rows, cols: term.cols }, 'PUT').catch(() => { lastSize = ''; });
    };

    // The iOS keyboard shrinks the visual viewport, not the layout one: size
    // the page to what is actually visible, then refit the terminal to it.
    const layout = () => {
      const vv = window.visualViewport;
      const el = page.current;
      const box = el?.parentElement;
      if (el && box && vv) {
        // The tab's own space, or less when the keyboard covers part of it.
        const top = box.getBoundingClientRect().top;
        el.style.height = `${Math.max(120, Math.min(box.clientHeight, vv.height + vv.offsetTop - top))}px`;
      }
      try { fit.fit(); } catch { /* not laid out yet */ }
      repaint();
      clearTimeout(sizeTimer);
      sizeTimer = window.setTimeout(sendSize, 250);
    };

    const connect = () => {
      clearTimeout(retry);
      setState('connecting');
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/api/code/pty/${id}/ws?cursor=${cursor}`);
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => { setState('open'); lastSize = ''; layout(); };
      ws.onmessage = ev => {
        if (typeof ev.data === 'string') { term.write(ev.data, repaint); return; }
        // A binary frame is OpenCode's bookkeeping: 0x00 then {"cursor": n}.
        const bytes = new Uint8Array(ev.data);
        if (bytes[0] === 0) {
          try { cursor = JSON.parse(new TextDecoder().decode(bytes.slice(1))).cursor ?? cursor; } catch { /* ignore */ }
        } else {
          term.write(bytes);
        }
      };
      ws.onclose = ev => {
        ws = null;
        if (closedByUs) return;
        if (ev.code === 4401 || ev.code === 4403) { onLocked(); return; }
        if (ev.code === 4404) { setState('ended'); return; }
        setState('lost');
        // iOS closes sockets of apps off screen; come back from where we were.
        if (document.visibilityState === 'visible') retry = window.setTimeout(connect, 1500);
      };
    };

    const send = (data: string) => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(data); };
    term.onData(data => {
      if (ctrlRef.current && data.length === 1 && /[a-z@\[\\\]^_ ]/i.test(data)) {
        ctrlRef.current = false;
        setCtrl(false);
        send(data === ' ' ? '\x00' : String.fromCharCode(data.toUpperCase().charCodeAt(0) & 0x1f));
        return;
      }
      send(data);
    });
    api.current = {
      send, term,
      zoom: d => {
        term.options.fontSize = Math.max(8, Math.min(20, (term.options.fontSize || 12) + d));
        try { localStorage.setItem(FONT_KEY, String(term.options.fontSize)); } catch { /* ignore */ }
        layout();
      },
    };

    const onVisible = () => { if (document.visibilityState === 'visible' && !ws && !closedByUs) connect(); };
    window.visualViewport?.addEventListener('resize', layout);
    addEventListener('resize', layout);
    document.addEventListener('visibilitychange', onVisible);
    layout();
    connect();
    return () => {
      closedByUs = true;
      clearTimeout(retry);
      window.visualViewport?.removeEventListener('resize', layout);
      removeEventListener('resize', layout);
      document.removeEventListener('visibilitychange', onVisible);
      ws?.close();
      term.dispose();
    };
  }, [id]);

  const key = (s: string) => { api.current.send(s); api.current.term?.focus(); };
  const app = () => !!api.current.term?.modes.applicationCursorKeysMode;

  const kill = async () => {
    if (!confirm('Chiudere il terminale? Quello che gira dentro viene fermato.')) return;
    try { await codePost(`/api/code/pty/${id}`, {}, 'DELETE'); } catch { /* already gone */ }
    location.hash = '#/codice';
  };

  return (
    <section class="sub-page term-page" ref={page}>
      <header class="chat-head">
        <a class="head-btn" href="#/codice" title="Indietro (il terminale resta aperto)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </a>
        <div class="chat-title">
          <span>{info?.title || 'Terminale'}</span>
          <small>{state === 'open' ? info?.dir : state === 'connecting' ? 'collego…' : state === 'lost' ? 'riconnessione…' : 'terminato'}</small>
        </div>
        <button class="head-btn text-btn" onClick={() => api.current.zoom(-1)} title="Testo più piccolo">A−</button>
        <button class="head-btn text-btn" onClick={() => api.current.zoom(1)} title="Testo più grande">A+</button>
        <button class="head-btn" onClick={kill} title="Chiudi il terminale">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </header>
      <div class="term-host" ref={host} onClick={() => api.current.term?.focus()} />
      <div class="term-keys">
        <button class={ctrl ? 'is-on' : ''}
                onClick={() => { ctrlRef.current = !ctrlRef.current; setCtrl(ctrlRef.current); api.current.term?.focus(); }}>ctrl</button>
        {KEYS.map(k => <button key={k.label} onClick={() => key(k.send(app()))}>{k.label}</button>)}
      </div>
    </section>
  );
}
