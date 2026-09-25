import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { api } from '../lib/sse';

// Hermes' own log files come from the dashboard (with level filtering); the
// services' journals from the root-side helper, for the watched units only.
const SOURCES: { id: string; label: string }[] = [
  { id: 'agent', label: 'Hermes' },
  { id: 'errors', label: 'Hermes, solo errori' },
  { id: 'gateway', label: 'Gateway' },
  { id: 'unit:hermes-agent', label: 'Servizio Hermes (journal)' },
  { id: 'unit:hermes-dashboard', label: 'Dashboard (journal)' },
  { id: 'unit:hermes-hub', label: 'Hub (journal)' },
  { id: 'unit:ngrok-tunnel', label: 'Tunnel ngrok (journal)' },
  { id: 'unit:hermes-webui', label: 'Web UI (journal)' },
  { id: 'unit:warp-svc', label: 'WARP (journal)' },
];

const LEVELS = [['', 'tutti'], ['INFO', 'info'], ['WARNING', 'avvisi'], ['ERROR', 'errori']];
const AUTO_MS = 5000;

function lineClass(line: string) {
  if (/\b(ERROR|CRITICAL|Traceback|FAIL)/.test(line)) return 'l-err';
  if (/\bWARN(ING)?\b/.test(line)) return 'l-warn';
  return '';
}

export function Logs() {
  const [source, setSource] = useState('agent');
  const [level, setLevel] = useState('');
  const [search, setSearch] = useState('');
  const [auto, setAuto] = useState(true);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const journal = source.startsWith('unit:');

  const load = async () => {
    const q = new URLSearchParams({ source, lines: '300' });
    if (level && !journal) q.set('level', level);
    if (search.trim().length >= 2) q.set('search', search.trim());
    try {
      const d = await api<{ lines: string[] }>(`/api/server/logs?${q}`);
      setLines(d.lines);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    const t = setTimeout(load, search ? 350 : 0);
    return () => clearTimeout(t);
  }, [source, level, search]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => { if (document.visibilityState === 'visible') load(); }, AUTO_MS);
    return () => clearInterval(t);
  }, [auto, source, level, search]);

  // Newest at the bottom, followed while the reader is there.
  useLayoutEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <section class="sub-page">
      <header class="chat-head">
        <a class="head-btn" href="#/server" title="Torna al server">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </a>
        <div class="chat-title"><span>Log</span></div>
        <button class={`head-btn${auto ? ' is-on' : ''}`} title={auto ? 'Aggiornamento automatico attivo' : 'Aggiornamento automatico spento'}
                onClick={() => setAuto(!auto)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5" /></svg>
        </button>
      </header>
      <div class="log-controls">
        <select value={source} onChange={e => setSource((e.target as HTMLSelectElement).value)}>
          {SOURCES.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        <select value={level} disabled={journal} onChange={e => setLevel((e.target as HTMLSelectElement).value)}>
          {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input type="search" placeholder="cerca" value={search}
               onInput={e => setSearch((e.target as HTMLInputElement).value)} />
      </div>
      <div class="log-lines" ref={box}
           onScroll={() => { const el = box.current!; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
        {error && <p class="list-note">{error}</p>}
        {!error && !lines.length && <p class="list-note">nessuna riga</p>}
        {lines.map((l, i) => <div key={i} class={lineClass(l)}>{l}</div>)}
      </div>
    </section>
  );
}
