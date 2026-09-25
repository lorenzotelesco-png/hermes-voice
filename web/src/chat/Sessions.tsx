import { useEffect, useRef, useState } from 'preact/hooks';
import { api } from '../lib/sse';
import { chat } from './store';
import { sourceLabel, when } from './labels';

interface Row {
  id: string;
  source: string;
  title: string | null;
  preview: string | null;
  last_active: number | null;
  message_count?: number;
  snippet?: string;
}

const PAGE = 30;

// The dashboard marks matches as >>>word<<<. Split rather than inject HTML:
// the snippet is conversation text, and conversation text is never markup.
function Snippet({ text }: { text: string }) {
  const parts = text.split(/>>>|<<</);
  return <p class="row-sub">{parts.map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : p))}</p>;
}

function open(id: string) {
  if (id !== chat.snapshot.sessionId) chat.open(id);
  location.hash = '#/chat';
}

export function Sessions() {
  const [rows, setRows] = useState<Row[]>([]);
  const [more, setMore] = useState(false);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<Row[] | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const searchSeq = useRef(0);

  const page = async (offset: number) => {
    setBusy(true);
    try {
      const data = await api(`/api/sessions?limit=${PAGE}&offset=${offset}`);
      setRows(r => (offset ? [...r, ...data.sessions] : data.sessions));
      setMore(data.has_more);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
    setBusy(false);
  };

  useEffect(() => { page(0); }, []);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits(null); return; }
    const seq = ++searchSeq.current;
    const t = setTimeout(async () => {
      try {
        const data = await api(`/api/sessions/search?q=${encodeURIComponent(term)}`);
        if (seq === searchSeq.current) {
          setHits(data.results.map((h: any) => ({ ...h, id: h.session_id, preview: null })));
        }
      } catch (e: any) {
        if (seq === searchSeq.current) setError(e.message);
      }
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const list = hits ?? rows;
  const current = chat.snapshot.sessionId;

  return (
    <section class="sessions">
      <header class="chat-head">
        <a class="head-btn" href="#/chat" title="Torna alla chat">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7" /></svg>
        </a>
        <div class="chat-title"><span>Conversazioni</span></div>
        <button class="head-btn" title="Nuova conversazione"
                onClick={() => { chat.newChat(); location.hash = '#/chat'; }}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </header>
      <div class="search">
        <input type="search" placeholder="Cerca in tutte le conversazioni" value={q}
               onInput={e => setQ((e.target as HTMLInputElement).value)} />
      </div>
      <div class="list">
        {error && <p class="list-note">Non riesco a caricare: {error}</p>}
        {hits && !hits.length && <p class="list-note">Nessun risultato</p>}
        {list.map(r => (
          <button key={r.id} class={`row${r.id === current ? ' is-current' : ''}`} onClick={() => open(r.id)}>
            <div class="row-top">
              <span class={`src src-${r.source}`}>{sourceLabel(r.source)}</span>
              <span class="row-title">{r.title || r.preview || 'Senza titolo'}</span>
              <time>{when(r.last_active)}</time>
            </div>
            {r.snippet
              ? <Snippet text={r.snippet} />
              : r.title && r.preview && <p class="row-sub">{r.preview}</p>}
          </button>
        ))}
        {!hits && more && (
          <button class="more" disabled={busy} onClick={() => page(rows.length)}>
            {busy ? 'carico…' : 'Altre conversazioni'}
          </button>
        )}
        {busy && !rows.length && <p class="list-note">carico…</p>}
      </div>
    </section>
  );
}
